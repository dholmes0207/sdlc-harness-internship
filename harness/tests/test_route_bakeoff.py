"""test_route_bakeoff.py — probe-set structural validator for skill routing.

route_bakeoff turns the 54 skill `description` strings (the router contract) into a
cheap regression surface. The $0 structural pass (no LLM) parses every description
into a catalog and checks a human-edited probe-set is internally consistent against
it — every probe targets a skill that exists (or the __none__ sentinel) and no
distractor doubles as a probe target.

HONEST framing (the validator does NOT measure routing): the structural pass fires
no router and scores no over/under-trigger. Real indirect / context-only / none-clean
rates require firing the router prompt and only run under --run-llm (opt-in, needs a
runner). The three rate axes are reported SEPARATELY so over-trigger (none-clean) is
never averaged into under-trigger.

Contract mirrors check_report_language / check_skill_structure: advisory exit 0 by
default, --strict exits non-zero on a FAIL verdict, a missing probe-set skips clean.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import route_bakeoff as rb  # noqa: E402


# --- helpers ------------------------------------------------------------------

def _skill(root: Path, name: str, description: str) -> None:
    """Write a minimal skill dir with a SKILL.md carrying name + description."""
    d = root / name.split(":")[-1]
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: %s\ndescription: %s\n---\n\n# %s\n\nbody\n" % (name, description, name),
        encoding="utf-8",
    )


def _write_probes(path: Path, probes, distractors=None) -> Path:
    import yaml
    path.write_text(
        yaml.safe_dump({"probes": probes, "distractors": distractors or []}, sort_keys=False),
        encoding="utf-8",
    )
    return path


# --- catalog ------------------------------------------------------------------

def test_parse_catalog_from_skill_dirs(tmp_path):
    """build_catalog parses each SKILL.md frontmatter (via frontmatter_parser) into
    a name -> description map. Two skill dirs => two entries."""
    root = tmp_path / "skills"
    _skill(root, "hs:plan", "Create a verified implementation plan before cook.")
    _skill(root, "hs:cook", "Execute an approved plan phase by phase with TDD.")

    catalog = rb.build_catalog(root)

    assert set(catalog) == {"hs:plan", "hs:cook"}
    assert catalog["hs:plan"].startswith("Create a verified")
    assert catalog["hs:cook"].startswith("Execute an approved")


# --- three rate axes reported separately --------------------------------------

def test_none_clean_axis_reported_separately(tmp_path):
    """none-clean (over-trigger) is its own axis, never folded into the
    indirect/context-only (under-trigger) axes."""
    root = tmp_path / "skills"
    _skill(root, "hs:plan", "Create a verified implementation plan before cook.")
    catalog = rb.build_catalog(root)
    probes = rb.load_probes(_write_probes(
        tmp_path / "route-probes.yaml",
        [
            {"target": "hs:plan", "condition": "indirect", "message": "help me design the rework"},
            {"target": "__none__", "condition": "none", "message": "what's the weather"},
        ],
    ))

    result = rb.score_structural(catalog, probes)

    # All three axes are distinct keys in the verdict (value None until --run-llm).
    assert "none_clean_rate" in result
    assert "indirect_rate" in result
    assert "context_only_rate" in result
    assert result["none_clean_rate"] is None
    assert result["indirect_rate"] is None


# --- advisory exit codes ------------------------------------------------------

def test_advisory_exit_zero_default(tmp_path, capsys):
    """Default run is advisory: exit 0 even when a probe is misconfigured."""
    root = tmp_path / "skills"
    _skill(root, "hs:plan", "Create a verified implementation plan before cook.")
    probes_path = _write_probes(
        tmp_path / "route-probes.yaml",
        [{"target": "hs:does-not-exist", "condition": "indirect", "message": "x"}],
    )

    rc = rb.main([str(root), "--probes", str(probes_path)])
    out = capsys.readouterr().out

    assert rc == 0
    verdict = json.loads(out)
    assert verdict["verdict"] == "FAIL"  # reported as FAIL, but advisory => exit 0


def test_strict_blocks_on_low_none_clean(tmp_path):
    """--strict exits non-zero when a measured routing rate is below threshold.
    none-clean is measured only under --run-llm, so a runner that over-triggers on
    the none probe drives the rate down and --strict blocks."""
    root = tmp_path / "skills"
    _skill(root, "hs:plan", "Create a verified implementation plan before cook.")
    probes_path = _write_probes(
        tmp_path / "route-probes.yaml",
        [{"target": "__none__", "condition": "none", "message": "what's the weather"}],
    )

    # Runner that wrongly fires hs:plan on the none probe => none-clean = 0.0.
    def over_trigger(_prompt, _message, _catalog):
        return "hs:plan"

    rc = rb.main([str(root), "--probes", str(probes_path), "--run-llm", "--strict"],
                 runner=over_trigger)
    assert rc == 1


def test_skip_clean_when_no_probes(tmp_path, capsys):
    """No probe-set at the resolved path => skip clean (exit 0, named) — mirrors
    check_report_language degrading when its base ref can't be resolved."""
    root = tmp_path / "skills"
    _skill(root, "hs:plan", "Create a verified implementation plan before cook.")
    missing = tmp_path / "route-probes.yaml"  # never written

    rc = rb.main([str(root), "--probes", str(missing)])
    out = capsys.readouterr().out.lower()

    assert rc == 0
    assert "skip" in out


# --- distractor structural rule -----------------------------------------------

def test_distractor_must_not_win(tmp_path):
    """A distractor present in the catalog but not used as a probe target is fine.
    A probe whose target IS a distractor is a config contradiction => fail-config."""
    root = tmp_path / "skills"
    _skill(root, "hs:plan", "Create a verified implementation plan before cook.")
    _skill(root, "hs:onboard", "Onboard a new contributor to the repo.")
    catalog = rb.build_catalog(root)

    # Case A: onboard is a distractor, not a target => PASS.
    ok = rb.score_structural(catalog, rb.load_probes(_write_probes(
        tmp_path / "ok.yaml",
        [{"target": "hs:plan", "condition": "indirect", "message": "design the rework"}],
        distractors=["hs:onboard"],
    )))
    assert ok["verdict"] == "PASS"
    assert not any(f["rule"] == "distractor-is-target" for f in ok["config_findings"])

    # Case B: a probe targets the distractor => contradiction => FAIL.
    bad = rb.score_structural(catalog, rb.load_probes(_write_probes(
        tmp_path / "bad.yaml",
        [{"target": "hs:onboard", "condition": "indirect", "message": "design the rework"}],
        distractors=["hs:onboard"],
    )))
    assert bad["verdict"] == "FAIL"
    assert any(f["rule"] == "distractor-is-target" for f in bad["config_findings"])
