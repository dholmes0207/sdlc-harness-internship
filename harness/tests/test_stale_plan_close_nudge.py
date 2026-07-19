"""test_stale_plan_close_nudge.py — the advisory that catches a plan cook left
verified-PASS but still in_progress, the exact state that pins the gate's
active-plan resolver to a stale plan and blocks unrelated shipping.

Detector is keyed on the machine artifact (verification.json verdict PASS), not
the human checkbox, so it fires precisely when close_plan would have helped and
cook forgot to run it. Nudge posture: advisory, fail-open, never blocks.
"""
import json
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for p in (_HOOKS, _SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import stale_plan_close_nudge as nud  # noqa: E402


def _mk_plan(root, name="260621-2100-demo", status="in_progress", verdict="PASS"):
    d = root / "plans" / name
    (d / "artifacts").mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: %s\n---\n\n# Body\n" % (name, status),
        encoding="utf-8")
    if verdict is not None:
        (d / "artifacts" / "verification.json").write_text(
            json.dumps({"stage": "push", "plan": name, "actor": "user:a@x",
                        "ts": "t", "checks": [{"name": "x", "status": "PASS"}],
                        "verdict": verdict}), encoding="utf-8")
    return d


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    return tmp_path


def test_detects_verified_pass_but_in_progress(root):
    d = _mk_plan(root, status="in_progress", verdict="PASS")
    assert nud.stale_done_plan(root) == d


def test_no_nudge_when_no_verification(root):
    _mk_plan(root, status="in_progress", verdict=None)
    assert nud.stale_done_plan(root) is None


def test_no_nudge_when_verdict_not_pass(root):
    _mk_plan(root, status="in_progress", verdict="FAIL")
    assert nud.stale_done_plan(root) is None


def test_no_nudge_when_already_completed(root):
    # resolver only ever returns in_progress plans, so a completed plan is
    # never a candidate — nothing to nudge.
    _mk_plan(root, status="completed", verdict="PASS")
    assert nud.stale_done_plan(root) is None


def test_core_emits_only_for_publish_skills(root):
    _mk_plan(root, status="in_progress", verdict="PASS")
    # core() now RETURNS the advisory string (routing to the configured sink is
    # the caller's job via emit_nudge); it no longer writes stderr itself.
    msg = nud.core({"tool_name": "Skill", "tool_input": {"skill": "hs:ship"}})
    assert "close_plan" in (msg or "")
    # a non-publish skill stays silent even with a stale done plan present
    assert nud.core({"tool_name": "Skill", "tool_input": {"skill": "hs:scout"}}) is None


def test_core_silent_when_nothing_stale(root, capsys):
    _mk_plan(root, status="in_progress", verdict=None)
    nud.core({"tool_name": "Skill", "tool_input": {"skill": "hs:ship"}})
    assert capsys.readouterr().err == ""


# --- broadened detector: a cooked-PASS plan stuck at pending/draft is the gap
# the in_progress-only resolver never sees (it is the dominant real failure mode:
# cook wrote a PASS artifact but neither open_plan nor close_plan ran). ---

def test_cooked_open_plans_catches_pending_with_pass(root):
    d = _mk_plan(root, name="260101-0000-pend", status="pending", verdict="PASS")
    names = {p.name for p in nud.cooked_open_plans(root)}
    assert d.name in names


def test_cooked_open_plans_catches_draft_with_pass(root):
    d = _mk_plan(root, name="260101-0001-draft", status="draft", verdict="PASS")
    assert d.name in {p.name for p in nud.cooked_open_plans(root)}


def test_cooked_open_plans_catches_approved_with_pass(root):
    # `approved` is an open pre-cook state: a PASS sitting under it is still
    # cooked-but-not-closed, so the nudge must catch it.
    d = _mk_plan(root, name="260101-0001-appr", status="approved", verdict="PASS")
    assert d.name in {p.name for p in nud.cooked_open_plans(root)}


def test_cooked_open_plans_includes_in_progress(root):
    d = _mk_plan(root, name="260101-0002-inprog", status="in_progress", verdict="PASS")
    assert d.name in {p.name for p in nud.cooked_open_plans(root)}


def test_cooked_open_plans_excludes_completed_and_unpassed(root):
    _mk_plan(root, name="260101-0003-done", status="completed", verdict="PASS")
    _mk_plan(root, name="260101-0004-nopass", status="pending", verdict=None)
    names = {p.name for p in nud.cooked_open_plans(root)}
    assert "260101-0003-done" not in names
    assert "260101-0004-nopass" not in names


def test_core_emits_for_pending_pass_on_ship(root):
    # the case the legacy single-plan detector silently missed
    _mk_plan(root, name="260101-0005-pend", status="pending", verdict="PASS")
    msg = nud.core({"tool_name": "Skill", "tool_input": {"skill": "hs:ship"}}) or ""
    assert "260101-0005-pend" in msg
    assert "reconcile_plan_status" in msg  # points at the bulk tool
