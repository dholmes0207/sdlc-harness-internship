"""test_analyze_telemetry.py — registry-driven lens CLI + honesty gate.

analyze_telemetry is the deterministic read-only front for the telemetry lenses.
The build ships ONE lens (workflow_chains); the registry is the single extension point,
and analyze must NOT hard-import the not-yet-shipped lenses. The markdown
formatter ALWAYS carries a "NOT measured" honesty section — a gate against
reading partial telemetry as full coverage.

Covers:
  - honesty gate: md output always has the "NOT measured" section
  - registry: a registry row whose module does not import → analyze still runs,
    output visibly notes the lens absent (never a silent drop)
  - empty registry → analyze still runs + renders the honesty section
  - the workflow lens runs through the registry on real fixture data (json)
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import analyze_telemetry as at  # noqa: E402
import lens_workflow_chains as lwc  # noqa: E402 — each lens now owns its render()


def test_workflow_deviations_caveated_when_gated():
    # consistency with skill_usage: on a low-volume (gated) corpus the lens must
    # not present a 1-count "deviation" as an actionable finding.
    agg = {"lens": "workflow_chains", "sessions_analyzed": 2, "sufficient": False,
           "gated": True, "common_chains": [{"chain": "a → b", "count": 1}],
           "deviations": [{"chain": "a → b", "count": 1}], "declared_chains": []}
    out = lwc.render(agg)
    assert "gated: True" in out
    assert "Deviations (undeclared chains)" not in out   # not presented as finding
    assert "sparse" in out.lower() or "insufficient" in out.lower()


def test_workflow_deviations_shown_when_sufficient():
    agg = {"lens": "workflow_chains", "sessions_analyzed": 8, "sufficient": True,
           "gated": False, "common_chains": [],
           "deviations": [{"chain": "a → b", "count": 4}], "declared_chains": []}
    out = lwc.render(agg)
    assert "Deviations (undeclared chains)" in out


def _mk_skill(sdir: Path, dirname: str, name: str):
    d = sdir / dirname
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: %s\n---\n" % name, encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Skills catalog + declared-chains fixture + telemetry sink, all under tmp."""
    sdir = tmp_path / "skills"
    for d, n in (("hs-plan", "hs:plan"), ("hs-cook", "hs:cook"), ("hs-test", "hs:test")):
        _mk_skill(sdir, d, n)
    chains = tmp_path / "skill-chains.yaml"
    chains.write_text(
        "chains:\n  - [hs:plan, hs:cook]\n  - [hs:cook, hs:test]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARNESS_SKILLS_DIR", str(sdir))
    monkeypatch.setenv("HARNESS_SKILL_CHAINS", str(chains))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    return {"skills": sdir, "chains": chains, "tmp": tmp_path}


def _write_invocations(tmp_path, rows):
    from datetime import datetime, timezone
    d = tmp_path / "state" / "telemetry"
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with open(d / "invocations.jsonl", "w", encoding="utf-8") as fh:
        for sess, skill in rows:
            fh.write(json.dumps({"session": sess, "skill": skill, "ts": now}) + "\n")


class TestHonestyGate:
    def test_md_overview_always_has_not_measured_section(self, env, capsys):
        rc = at.main(["--lens", "all", "--format", "md"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "NOT measured" in out

    def test_not_measured_lists_unshipped_lenses(self, env, capsys):
        at.main(["--lens", "all", "--format", "md"])
        out = capsys.readouterr().out
        # at least one not-yet-shipped lens named as not-measured (no coverage illusion)
        assert "reliability" in out or "usage" in out


class TestRegistry:
    def test_absent_registry_lens_is_visibly_noted(self, env, capsys, monkeypatch):
        # A registry row whose module cannot import must surface as a visible
        # absence, never a silent drop.
        monkeypatch.setattr(at, "LENS_REGISTRY", {
            "ghost": ("lens_does_not_exist", lambda m, a: m.gather()),
        })
        rc = at.main(["--lens", "all", "--format", "md"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "ghost" in out
        assert "NOT measured" in out

    def test_empty_registry_still_runs_and_renders_honesty(self, env, capsys, monkeypatch):
        monkeypatch.setattr(at, "LENS_REGISTRY", {})
        rc = at.main(["--lens", "all", "--format", "md"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "NOT measured" in out

    def test_unknown_lens_name_is_rejected(self, env, capsys):
        rc = at.main(["--lens", "nope", "--format", "json"])
        assert rc == 2

    def test_single_lens_error_is_isolated_not_a_traceback(self, env, capsys, monkeypatch):
        # A single lens that raises (here: the workflow lens fail-loud on a
        # missing skill-chains file) must degrade to a VISIBLE error entry and
        # exit 0 — the SAME posture as the overview path, never a raw traceback /
        # exit 1. gather_all isolates each lens; gather_lens must too.
        monkeypatch.setenv("HARNESS_SKILL_CHAINS",
                           str(env["tmp"] / "does-not-exist.yaml"))
        rc = at.main(["--lens", "workflow", "--format", "json"])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["lens"] == "workflow"
        assert "error" in data


class TestWorkflowThroughRegistry:
    def test_workflow_lens_gathers_through_registry_json(self, env, capsys):
        _write_invocations(env["tmp"], [
            ("s1", "hs:plan"), ("s1", "hs:cook"),
            ("s2", "hs:cook"), ("s2", "hs:test"),
        ])
        rc = at.main(["--lens", "workflow", "--format", "json", "--days", "30"])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["lens"] == "workflow_chains"
        chains = {c["chain"] for c in data["common_chains"]}
        assert "hs-plan → hs-cook" in chains


def test_not_measured_no_longer_lists_session():
    joined = " ".join(at.NOT_MEASURED)
    # the unshipped-lens list no longer starts with session (it is now shipped)
    assert "session, health" not in joined and "session, gate" not in joined
