"""test_privacy_approved_prefix.py — APPROVED: override for the privacy read gate (B9).

Flow: Read ".env" is BLOCKED; the human approves; the agent retries Read
"APPROVED:<session_token>:.env" which the gate ALLOWS (and audit-logs). Sensitivity must
still be detected WITH the prefix (so the override is recognized as an override, not a
non-secret), and the prefix is stripped from the input that actually reaches the Read tool.

SESSION-SCOPED APPROVAL (mitigates replay): the approval token includes the first 12 chars
of the session_id so an agent that learns the pattern in one session cannot replay it in
another — and the bypass instruction is no longer printed in the block message.
"""
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parents[1] / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import privacy_read_guard as prg  # noqa: E402

_SID = "abc123-def4-5678"  # test session_id (first 12 chars = "abc123-def4")
_TOKEN = prg._session_token({"session_id": _SID})


def _read(path, sid=_SID):
    return {"tool_name": "Read", "tool_input": {"file_path": path},
            "session_id": sid}


def _ctx(sid=_SID):
    return {"session_id": sid}


def test_has_and_strip_prefix():
    approved = f"APPROVED:{_TOKEN}:.env"
    assert prg._has_approval(approved, _ctx())
    assert not prg._has_approval("APPROVED:.env", _ctx())  # no session token
    assert not prg._has_approval(".env", _ctx())
    assert prg._strip_approval(f"APPROVED:{_TOKEN}:/p/.env", _ctx()) == "/p/.env"
    assert prg._strip_approval(".env", _ctx()) == ".env"


def test_sensitivity_detected_with_prefix():
    approved = f"APPROVED:{_TOKEN}:.env"
    assert prg._is_sensitive(approved, _ctx())
    assert prg._is_sensitive(".env", _ctx())
    assert not prg._is_sensitive(f"APPROVED:{_TOKEN}:app.py", _ctx())
    assert not prg._is_sensitive("app.py", _ctx())


def test_core_blocks_unapproved_secret():
    reason = prg.core(_read(".env"))
    assert isinstance(reason, str) and "approval" in reason.lower()
    # Block message must NOT teach the bypass pattern
    assert "APPROVED:" not in reason


def test_core_allows_approved_secret():
    approved = f"APPROVED:{_TOKEN}:.env"
    assert prg.core(_read(approved)) is None


def test_core_allows_normal_file():
    assert prg.core(_read("app.py")) is None


def test_allow_payload_strips_prefix():
    approved = f"APPROVED:{_TOKEN}:/p/.env"
    out = prg._allow_stripped_output(_read(approved))
    inp = out["hookSpecificOutput"]["updatedInput"]
    assert inp["file_path"] == "/p/.env"
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
