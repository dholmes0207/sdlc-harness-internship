"""test_receipts_gate_wiring.py — producer↔consumer wiring for the remote gate.

LESSONS: a shipped producer whose downstream never calls it is dead. This pins that
receipts-gate.yml actually invokes the two real scripts, at the right stages, on the
right triggers — a static check the workflow YAML cannot self-test."""
from pathlib import Path

import pytest

# Dev-repo-only: asserts .github/workflows/receipts-gate.yml wiring, present in the
# development checkout but not in an installed bundle — skipped on installed copies.
pytestmark = pytest.mark.dev_repo

yaml = pytest.importorskip("yaml")

_REPO = Path(__file__).resolve().parents[2]
_WF = _REPO / ".github" / "workflows" / "receipts-gate.yml"


def _text():
    return _WF.read_text(encoding="utf-8")


def test_invokes_real_scripts_that_exist():
    t = _text()
    assert "harness/scripts/pr_changed_plans.py" in t
    assert "harness/scripts/artifact_check_cli.py" in t
    assert (_REPO / "harness" / "scripts" / "pr_changed_plans.py").is_file()
    assert (_REPO / "harness" / "scripts" / "artifact_check_cli.py").is_file()


def _resolve_run():
    doc = yaml.safe_load(_text())
    job = doc["jobs"]["receipts"]
    for step in job["steps"]:
        if step.get("id") == "resolve":
            return step["run"]
    raise AssertionError("no resolve step in receipts-gate.yml")


def test_pr_uses_pr_stage_push_main_uses_merge_stage():
    # M2/VL-11: a PR judges at `pr` grade; a direct push to main judges at `merge`
    # grade. Assert the EVENT→STAGE mapping (not mere string presence — a swapped
    # mapping would still contain both strings).
    run = _resolve_run()
    assert "else" in run, "resolve step must branch pull_request vs push"
    then_part, else_part = run.split("else", 1)
    assert "pull_request" in then_part and "stage=pr" in then_part
    assert "stage=merge" in else_part
    assert "stage=pr" not in else_part and "stage=merge" not in then_part


def test_triggers_pull_request_and_push_main():
    doc = yaml.safe_load(_text())
    on = doc.get(True) or doc.get("on")  # YAML parses bare `on:` as boolean True
    assert "pull_request" in on
    push = on.get("push") or {}
    assert "main" in (push.get("branches") or [])


def test_declares_minimal_permissions():
    # The gate only checks out + reads the diff + runs two read-only scripts, so it
    # should not inherit the broad default token — pin contents: read.
    doc = yaml.safe_load(_text())
    assert doc["permissions"]["contents"] == "read"


def test_has_cancel_in_progress_concurrency():
    doc = yaml.safe_load(_text())
    assert doc["concurrency"]["cancel-in-progress"] is True
