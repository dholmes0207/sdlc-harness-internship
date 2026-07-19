"""test_manual_gate_integration.py — a required `manual` type rides the DoD gate.

When a tier-2 policy requires `manual` for a class, the DoD gate applies
manual-test admissibility: a claimed (or fabricated-anchor) manual check cannot
satisfy a HARD requirement; an anchored output with a valid human charter
co-sign can. Soft requirements only reject a fabricated anchor.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402
import manual_test as mt  # noqa: E402


def _repo(tmp_path):
    """A repo root with a tier-2 policy requiring a hard `manual` for bugfix, a
    team roster, a state dir for anchors, and a plan dir."""
    (tmp_path / "test-policy.yaml").write_text(
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  qa: { required: [manual], enforcement: hard }\n",
        encoding="utf-8")
    (tmp_path / "harness" / "data").mkdir(parents=True)
    (tmp_path / "harness" / "data" / "team.yaml").write_text(
        'reviewers: ["user:lead@x.com"]\nallow_self_review: false\n',
        encoding="utf-8")
    d = tmp_path / "plans" / "260624-0000-m"
    (d / "artifacts").mkdir(parents=True)
    (d / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    return d


def _state(tmp_path):
    return tmp_path / "harness" / "state"


def _seed_anchor(tmp_path, command):
    sink = _state(tmp_path) / "telemetry" / "manual-test-anchor.jsonl"
    sink.parent.mkdir(parents=True, exist_ok=True)
    aid = mt.anchor_id_for(command)
    sink.write_text(json.dumps({"anchor_id": aid}) + "\n", encoding="utf-8")
    return aid


def _verif(plan_dir, manual_check):
    (plan_dir / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": plan_dir.name, "actor": "user:dev",
        "ts": "2026-06-24T00:00:00+07:00", "verdict": "PASS",
        "checks": [dict(manual_check, name="manual")],
    }), encoding="utf-8")


def test_claimed_manual_fails_hard(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(_state(tmp_path)))
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    d = _repo(tmp_path)
    _verif(d, {"status": "PASS", "format": "manual", "evidence_tier": "claimed"})
    v = ac.evaluate_test_policy(d, "qa", ["src/a.py"], root=tmp_path)
    assert v.status == "FAIL"
    assert "manual" in v.reason


def test_anchored_without_cosign_fails_hard(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(_state(tmp_path)))
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    d = _repo(tmp_path)
    aid = _seed_anchor(tmp_path, "curl -s http://localhost/health")
    _verif(d, {"status": "PASS", "format": "manual", "evidence_tier": "anchored",
               "anchor_id": aid, "actor": "user:dev"})
    v = ac.evaluate_test_policy(d, "qa", ["src/a.py"], root=tmp_path)
    assert v.status == "FAIL"


def test_anchored_with_cosign_passes_hard(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(_state(tmp_path)))
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    d = _repo(tmp_path)
    aid = _seed_anchor(tmp_path, "curl -s http://localhost/health")
    _verif(d, {"status": "PASS", "format": "manual", "evidence_tier": "anchored",
               "anchor_id": aid, "actor": "user:dev",
               "charter_cosign": "user:lead@x.com"})
    v = ac.evaluate_test_policy(d, "qa", ["src/a.py"], root=tmp_path)
    assert v.status == "PASS", v.reason
