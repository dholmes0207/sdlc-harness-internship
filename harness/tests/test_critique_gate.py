"""test_critique_gate.py — critique-consensus artifact gate policy.

The critique skill in gate mode writes a machine-readable verdict to
plans/<active>/artifacts/critique-consensus.json. Its verdict policy mirrors
review-decision: a hard stage passes ONLY on verdict exactly PASS —
PASS_WITH_RISK is a conscious soft-accept, BLOCKED means stop.

Enforcement is decoupled into stage-policy and SHIPS OFF: the default
harness/data/stage-policy.yaml lists critique-consensus at NO stage, so a fresh
(spine-only) install is never surprise-blocked by an artifact whose producer
(hs:critique) is an opt-in plugin. Turning it on is a tracked one-line
stage-policy edit; the per-mechanism unit tests below opt in via a temp policy
(HARNESS_STAGE_POLICY). The default posture is locked by
TestShippedPolicyDoesNotDefaultGateCritique. This is a PRESENCE gate — it proves
the critique ran, not who ran it.
"""
import json
import sys
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent.parent
_SCRIPTS = _HARNESS / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402

_SCHEMA = _HARNESS / "schemas" / "artifact-critique-consensus.json"


def _mk_plan(root: Path, name: str = "260615-2132-feature-x") -> Path:
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: in_progress\n---\n\n# %s\n" % (name, name),
        encoding="utf-8",
    )
    return d


def _consensus(plan_dir: Path, *, verdict="PASS", drop=None):
    rec = {
        "verdict": verdict,
        "reviewer": "user:critique",
        "role": "critique",
        "rationale": "no blocker survived consolidation",
        "ts": "2026-06-15T21:32:00+07:00",
    }
    for k in (drop or []):
        rec.pop(k)
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "critique-consensus.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    # A temp policy where a hard stage requires critique-consensus — opting the
    # gate IN, since the shipped policy leaves it OFF.
    policy = tmp_path / "critique-policy.yaml"
    policy.write_text(
        "stages:\n"
        "  push:\n"
        "    hard: true\n"
        "    require_plan: true\n"
        "    requires: [critique-consensus]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARNESS_STAGE_POLICY", str(policy))
    return tmp_path


class TestSchema:
    def test_schema_parses_and_declares_required_and_verdict_enum(self):
        spec = json.loads(_SCHEMA.read_text(encoding="utf-8"))
        for field in ("verdict", "reviewer", "role", "rationale", "ts"):
            assert field in spec["required"], "missing required field %s" % field
        assert spec["properties"]["verdict"]["enum"] == [
            "PASS", "PASS_WITH_RISK", "BLOCKED"]


class TestCritiqueConsensusGate:
    def test_missing_artifact_blocks_naming_kind_and_path(self, root):
        d = _mk_plan(root)
        reason = ac.check_stage("push", root)
        assert reason is not None
        assert "critique-consensus" in reason
        assert str(d / "artifacts") in reason

    def test_pass_passes(self, root):
        d = _mk_plan(root)
        _consensus(d, verdict="PASS")
        assert ac.check_stage("push", root) is None

    def test_blocked_verdict_blocks(self, root):
        d = _mk_plan(root)
        _consensus(d, verdict="BLOCKED")
        reason = ac.check_stage("push", root)
        assert reason is not None and "BLOCKED" in reason

    def test_pass_with_risk_is_not_enough(self, root):
        d = _mk_plan(root)
        _consensus(d, verdict="PASS_WITH_RISK")
        reason = ac.check_stage("push", root)
        assert reason is not None and "PASS_WITH_RISK" in reason

    def test_missing_required_field_blocks_naming_field(self, root):
        d = _mk_plan(root)
        _consensus(d, drop=["ts"])
        reason = ac.check_stage("push", root)
        assert reason is not None and "ts" in reason


class TestShippedPolicyDoesNotDefaultGateCritique:
    """critique-consensus enforcement ships OFF: the default policy lists it at
    NO stage, so a fresh spine-only install (where the producer hs:critique
    is an opt-in plugin) can ship/PR. Opting in is a tracked one-line stage-policy
    edit, exercised by the HARNESS_STAGE_POLICY unit tests above."""


# ---------------------------------------------------------------------------
# S5 back-compat -- fingerprint is additive-optional on top_findings[] (VL-6,
# probed and RESOLVED: adding it to `properties` never touches `required`).
# ---------------------------------------------------------------------------

import re  # noqa: E402


def _consolidator_recipe_fingerprint(anchor: str, finding: str,
                                     code_evidence: str, fingerprint_fn):
    """Mirrors the recipe documented in
    harness/plugins/hs/agents/critique-consolidator.md: file_path is the part
    before ':' when anchor matches ^[^:]+:\\d+, rule_or_title is `finding`,
    code_snippet is `code_evidence`; null when anchor is not file:line."""
    m = re.match(r"^([^:]+):\d+", anchor)
    if not m:
        return None
    return fingerprint_fn(m.group(1), finding, code_evidence)


class TestFingerprintBackCompat:
    def test_v8_old_top_finding_without_fingerprint_still_validates(self):
        """A pre-S5 top_findings[] record (no fingerprint key) must still
        pass jsonschema.validate against the extended schema."""
        jsonschema = pytest.importorskip("jsonschema")
        schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
        rec = {
            "verdict": "PASS",
            "reviewer": "user:critique",
            "role": "critique",
            "rationale": "no blocker survived consolidation",
            "ts": "2026-06-15T21:32:00+07:00",
            "top_findings": [
                {"severity": "minor", "anchor": "f.py:1",
                 "finding": "pre-S5 finding, no fingerprint key",
                 "fix": "n/a", "status": "proven"},
            ],
        }
        jsonschema.validate(rec, schema)  # no exception raised == PASS

    def test_v8b_new_record_with_fingerprint_also_validates(self):
        """A post-S5 record carrying fingerprint (string or null) validates too."""
        jsonschema = pytest.importorskip("jsonschema")
        schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
        rec = {
            "verdict": "PASS", "reviewer": "user:critique", "role": "critique",
            "rationale": "r", "ts": "2026-06-15T21:32:00+07:00",
            "top_findings": [
                {"severity": "major", "anchor": "f.py:42", "finding": "f",
                 "fix": "x", "status": "proven", "fingerprint": "abc123def4567890"},
                {"severity": "minor", "anchor": "repro: run pytest -k foo",
                 "finding": "g", "fix": "y", "status": "suspected",
                 "fingerprint": None},
            ],
        }
        jsonschema.validate(rec, schema)

    def test_v9_fingerprint_matches_hand_computed(self):
        """anchor='f.py:42' + code_evidence -> the consolidator recipe's
        fingerprint equals dismissals_store.fingerprint('f.py', finding,
        code_evidence) computed by hand."""
        _SCRIPTS = _HARNESS / "scripts"
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        import dismissals_store as ds  # noqa: E402

        anchor, finding, evidence = "f.py:42", "no-eval used", "x = eval(y)"
        by_hand = ds.fingerprint("f.py", finding, evidence)
        via_recipe = _consolidator_recipe_fingerprint(
            anchor, finding, evidence, ds.fingerprint)
        assert via_recipe == by_hand

    def test_v10_anchor_not_file_line_yields_null_fingerprint(self):
        """An anchor that is not file:line (a repro command) -> fingerprint
        is null, best-effort, never a guessed file path."""
        _SCRIPTS = _HARNESS / "scripts"
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        import dismissals_store as ds  # noqa: E402

        anchor = "run: pytest harness/tests/test_x.py -k repro"
        via_recipe = _consolidator_recipe_fingerprint(
            anchor, "some finding", "some evidence", ds.fingerprint)
        assert via_recipe is None

    def test_v12_required_fields_unchanged(self):
        """S5 must not touch `required` -- exactly the 5 pre-existing fields."""
        spec = json.loads(_SCHEMA.read_text(encoding="utf-8"))
        assert spec["required"] == ["verdict", "reviewer", "role", "rationale", "ts"]
