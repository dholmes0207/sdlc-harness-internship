"""test_review_stage_floor.py — per-stage effort/rounds floor (opt-in).

The floor is a self-discipline tier: absent or malformed policy -> no-op.
Only blocks when review-policy parses cleanly AND stage_floor[stage].enabled.

All tests in this file are pure-unit: they use tmp_path fixtures and
monkeypatch so they never touch the shipped review-policy.yaml.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _mk_plan(root: Path, name: str = "260626-0100-floor-test",
             status: str = "in_progress") -> Path:
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: %s\n---\n\n# %s\n" % (name, status, name),
        encoding="utf-8",
    )
    return d


def _mk_verification(plan_dir: Path, *, verdict: str = "PASS") -> None:
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    rec = {
        "stage": "push", "plan": plan_dir.name, "actor": "user:alice",
        "ts": "2026-06-26T08:00:00+07:00",
        "checks": [{"name": "pytest", "status": "PASS"}],
        "verdict": verdict,
    }
    (a / "verification.json").write_text(json.dumps(rec), encoding="utf-8")


def _mk_review_decision(plan_dir: Path, *, verdict: str = "PASS",
                        effort: str = None, rounds_run: int = None,
                        strategy: str = None) -> None:
    """Write a review-decision artifact (YAML, the SSOT format)."""
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    rec = {"verdict": verdict, "reviewer": "user:bob", "role": "reviewer",
           "rationale": "looks correct"}
    if effort is not None:
        rec["effort"] = effort
    if rounds_run is not None:
        rec["rounds_run"] = rounds_run
    if strategy is not None:
        rec["strategy"] = strategy
    (a / "review-decision.yaml").write_text(yaml.safe_dump(rec), encoding="utf-8")


def _mk_review_policy(root: Path, stage: str, *,
                      enabled: bool = True,
                      min_effort: str = "high",
                      min_rounds: int = None) -> Path:
    """Write a minimal review-policy.yaml to root with one stage floor configured."""
    p = root / "review-policy.yaml"
    floor_entry = "    enabled: %s\n    min_effort: %s\n" % (
        "true" if enabled else "false", min_effort)
    if min_rounds is not None:
        floor_entry += "    min_rounds: %d\n" % min_rounds
    content = (
        "profiles:\n  default:\n    rounds: 1\n    compounding: false\n"
        "    per_aspect: false\n    blind_main_sub: false\n    refute: false\n"
        "    effort: low\n    scope: diff\n    aspects: [correctness]\n"
        "stage_floor:\n  %s:\n%scaps:\n  max_rounds: 5\n  max_lenses_per_round: 8\n"
        % (stage, floor_entry)
    )
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    monkeypatch.delenv("HARNESS_STAGE_POLICY", raising=False)
    # Point stage-policy at the shipped file (not the tmp_path)
    shipped = Path(_SCRIPTS).parent / "data" / "stage-policy.yaml"
    monkeypatch.setenv("HARNESS_STAGE_POLICY", str(shipped))
    return tmp_path


# ---------------------------------------------------------------------------
# Tests (written FIRST — should fail before implementation)
# ---------------------------------------------------------------------------

class TestFloorAbsentPolicy:
    def test_floor_absent_policy_is_noop(self, root, monkeypatch):
        """No review-policy.yaml in the env path -> check_stage('ship') behaves
        exactly as before: passes when artifact is present and valid."""
        d = _mk_plan(root)
        _mk_verification(d)
        # 'ship' stage requires review-decision + plan-approval; simplify by
        # testing _check_stage_floor directly with a non-existent policy path.
        nonexistent = root / "does-not-exist.yaml"
        monkeypatch.setenv("HARNESS_REVIEW_POLICY", str(nonexistent))
        result = ac._check_stage_floor(d, "ship", str(root))
        assert result is None, (
            "absent review-policy must be a NO-OP (got: %r)" % result)


class TestFloorDisabled:
    def test_floor_disabled_is_noop(self, root, monkeypatch):
        """Floor ship.enabled=false -> no-op even when effort is missing."""
        d = _mk_plan(root)
        _mk_review_decision(d, verdict="PASS")  # no effort field
        policy_path = _mk_review_policy(root, "ship", enabled=False, min_effort="high")
        monkeypatch.setenv("HARNESS_REVIEW_POLICY", str(policy_path))
        result = ac._check_stage_floor(d, "ship", str(root))
        assert result is None, (
            "disabled floor must be NO-OP (got: %r)" % result)


class TestFloorEnabled:
    def test_floor_enabled_effort_below_min_blocks(self, root, monkeypatch):
        """Floor ship.enabled=true, min_effort=high; effort=medium -> block."""
        d = _mk_plan(root)
        _mk_review_decision(d, verdict="PASS", effort="medium")
        policy_path = _mk_review_policy(root, "ship", enabled=True, min_effort="high")
        monkeypatch.setenv("HARNESS_REVIEW_POLICY", str(policy_path))
        result = ac._check_stage_floor(d, "ship", str(root))
        assert result is not None, "effort below floor must block"
        assert "medium" in result or "high" in result, (
            "block reason must name the effort values (got: %r)" % result)

    def test_floor_enabled_effort_meets_min_passes(self, root, monkeypatch):
        """Floor ship.enabled=true, min_effort=high; effort=max (>= high) -> pass."""
        d = _mk_plan(root)
        _mk_review_decision(d, verdict="PASS", effort="max")
        policy_path = _mk_review_policy(root, "ship", enabled=True, min_effort="high")
        monkeypatch.setenv("HARNESS_REVIEW_POLICY", str(policy_path))
        result = ac._check_stage_floor(d, "ship", str(root))
        assert result is None, (
            "effort >= floor should pass (got: %r)" % result)

    def test_floor_enabled_missing_effort_blocks(self, root, monkeypatch):
        """Floor ON, review-decision has no effort field -> block with actionable reason."""
        d = _mk_plan(root)
        _mk_review_decision(d, verdict="PASS")  # no effort
        policy_path = _mk_review_policy(root, "ship", enabled=True, min_effort="high")
        monkeypatch.setenv("HARNESS_REVIEW_POLICY", str(policy_path))
        result = ac._check_stage_floor(d, "ship", str(root))
        assert result is not None, "missing effort with floor ON must block"
        # reason must name the missing field and the floor minimum
        assert "effort" in result.lower(), (
            "block reason must name 'effort' (got: %r)" % result)
        assert "high" in result, (
            "block reason must name the min_effort (got: %r)" % result)

    def test_floor_enabled_rounds_below_min_blocks(self, root, monkeypatch):
        """Floor ON, min_rounds=3, rounds_run=1 -> block."""
        d = _mk_plan(root)
        _mk_review_decision(d, verdict="PASS", effort="high", rounds_run=1)
        policy_path = _mk_review_policy(root, "ship", enabled=True,
                                        min_effort="high", min_rounds=3)
        monkeypatch.setenv("HARNESS_REVIEW_POLICY", str(policy_path))
        result = ac._check_stage_floor(d, "ship", str(root))
        assert result is not None, "rounds_run below min_rounds must block"
        assert "round" in result.lower() or "3" in result, (
            "block reason must name rounds (got: %r)" % result)


class TestFloorMalformedPolicy:
    def test_floor_malformed_policy_is_noop(self, root, monkeypatch):
        """CRITICAL: a malformed review-policy.yaml must NOT block — NO-OP.

        A typo in an advisory knob file cannot brick the ship gate. This is
        the hardest acceptance criterion: fail-soft by design (self-discipline
        tier, not a real boundary)."""
        d = _mk_plan(root)
        _mk_verification(d)
        _mk_review_decision(d, verdict="PASS")
        # Write a malformed policy (not a YAML mapping)
        malformed = root / "bad-policy.yaml"
        malformed.write_text("- this: is\n- not: a mapping\n", encoding="utf-8")
        monkeypatch.setenv("HARNESS_REVIEW_POLICY", str(malformed))
        result = ac._check_stage_floor(d, "ship", str(root))
        assert result is None, (
            "malformed review-policy MUST be NO-OP, never block (got: %r)" % result)


class TestFloorE2E:
    def test_floor_e2e_emit_then_read(self, root, monkeypatch):
        """E2E: simulate recall emitting effort=high,rounds_run=3,strategy=ship-grade.

        floor ship.enabled=true, min_effort=high, min_rounds=3 -> pass.
        Lower effort=medium -> block.
        Closes the P2->P3 loop: the field the gate reads is the field the
        producer writes."""
        d = _mk_plan(root)
        policy_path = _mk_review_policy(root, "ship", enabled=True,
                                        min_effort="high", min_rounds=3)
        monkeypatch.setenv("HARNESS_REVIEW_POLICY", str(policy_path))

        # Producer writes effort=high, rounds_run=3, strategy=ship-grade
        _mk_review_decision(d, verdict="PASS", effort="high",
                            rounds_run=3, strategy="ship-grade")
        result = ac._check_stage_floor(d, "ship", str(root))
        assert result is None, (
            "effort=high + rounds=3 >= floor should pass (got: %r)" % result)

        # Now lower effort -> should block
        _mk_review_decision(d, verdict="PASS", effort="medium",
                            rounds_run=3, strategy="ship-grade")
        result = ac._check_stage_floor(d, "ship", str(root))
        assert result is not None, "effort=medium < high floor must block"


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

class TestReviewDecisionSchemaOptionalFields:
    def test_review_decision_schema_optional_fields_validate(self, root):
        """Artifact with/without the 3 optional fields both pass presence gate.
        _REQUIRED_FIELDS['review-decision'] must stay unchanged."""
        d = _mk_plan(root)
        # Without optional fields: must still satisfy required-fields check
        _mk_review_decision(d, verdict="PASS")
        rec, problem = ac._load_artifact(d, "review-decision")
        assert rec is not None and problem is None
        missing = [f for f in ac._REQUIRED_FIELDS["review-decision"]
                   if f not in rec]
        assert not missing, "required fields missing: %s" % missing

        # With optional fields: must also load cleanly
        _mk_review_decision(d, verdict="PASS", effort="high",
                            rounds_run=3, strategy="ship-grade")
        rec, problem = ac._load_artifact(d, "review-decision")
        assert rec is not None and problem is None
        assert rec.get("effort") == "high"
        assert rec.get("rounds_run") == 3
        assert rec.get("strategy") == "ship-grade"
        # Required fields contract unchanged
        assert set(ac._REQUIRED_FIELDS["review-decision"]) == {
            "verdict", "reviewer", "role", "rationale"}
