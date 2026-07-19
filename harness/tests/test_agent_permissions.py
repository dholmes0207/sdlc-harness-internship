"""Tests for agent_permissions — the pure decision logic behind agent_rbac_guard.

The detector answers: given a role (agent_type, or '_parent' for the top-level
agent), a write target, and a parsed permission table, is the write in-lane?

Additive-skip contract: an absent or roleless table
yields None (no decision) so a fresh install never bricks the fleet — the gate is
inert until an operator declares a permission table.
"""
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import agent_permissions as ap  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# decide: deny-list classification (Verdict: block_reason / soft_rule / allow)
# ---------------------------------------------------------------------------

import write_deny_policy as wdp  # noqa: E402

_SUB = "hs:developer"


def _policy(soft_rules=()):
    return wdp.assemble_policy(list(soft_rules))


def _decide(root, role, rel_target, soft_rules=()):
    return ap.decide(role, str(Path(root) / rel_target), _policy(soft_rules), [str(root)])


def test_subagent_core_blocks(tmp_path):
    v = _decide(tmp_path, _SUB, "harness/hooks/x.py")
    assert v.block_reason and v.soft_rule is None


def test_subagent_cage_disarm_blocks(tmp_path):
    assert _decide(tmp_path, _SUB, ".claude/settings.local.json").block_reason


def test_subagent_hard_binary_blocks(tmp_path):
    assert _decide(tmp_path, _SUB, "harness/plugins/hs/x.py").block_reason


def test_parent_hard_binary_allowed(tmp_path):
    v = _decide(tmp_path, ap.ROLE_PARENT, "harness/plugins/hs/x.py")
    assert v.block_reason is None and v.soft_rule is None


def test_parent_core_allowed(tmp_path):
    # the trusted main session is unrestricted on the tool-Write path (write_guard +
    # the Bash floor cover the global cases); the cage blocks the SUBAGENT, not main.
    assert _decide(tmp_path, ap.ROLE_PARENT, "harness/hooks/x.py").block_reason is None


def test_subagent_carve_tests_allowed(tmp_path):
    v = _decide(tmp_path, _SUB, "harness/tests/x.py")
    assert v.block_reason is None and v.soft_rule is None


def test_subagent_state_not_carved_blocks(tmp_path):
    assert _decide(tmp_path, _SUB, "harness/state/trace/x.jsonl").block_reason


def test_subagent_plain_source_allowed(tmp_path):
    # goal #1: a developer writes source out of the box, no overlay.
    v = _decide(tmp_path, _SUB, "src/app.tsx")
    assert v.block_reason is None and v.soft_rule is None


def test_subagent_memory_lane_allowed(tmp_path):
    assert _decide(tmp_path, _SUB, ".claude/agent-memory/x/m.md").block_reason is None


def test_subagent_soft_hit_allows_and_marks(tmp_path):
    v = _decide(tmp_path, _SUB, "docs/notes.md", [("deny", "docs/**")])
    assert v.block_reason is None
    assert v.soft_rule == "docs/**"


def test_decide_pure_no_io(tmp_path, monkeypatch):
    # PURE: clearing every HARNESS_* env changes nothing (no file/env read).
    for k in list(os.environ):
        if k.startswith("HARNESS_"):
            monkeypatch.delenv(k, raising=False)
    assert _decide(tmp_path, _SUB, "harness/hooks/x.py").block_reason


def test_decide_deterministic(tmp_path):
    args = (ap.ROLE_PARENT, str(tmp_path / "src/app.tsx"), _policy(), [str(tmp_path)])
    first = ap.decide(*args)
    for _ in range(50):
        assert ap.decide(*args) == first


def test_decide_unresolved_subagent_blocks(tmp_path):
    other = tmp_path.parent / "outside_decide_probe"
    other.mkdir(exist_ok=True)
    assert ap.decide(_SUB, str(other / "x.py"), _policy(), [str(tmp_path / "root")]).block_reason
