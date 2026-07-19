"""deny_audit: 3-event audit (hard/soft/widen) over trace_log's chain store, a
SOFT-only widen gate (the hard floor has no appeal), and a tamper-evident verifier."""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
for _p in (_SCRIPTS, _HOOKS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import deny_audit  # noqa: E402
import trace_log  # noqa: E402
import hook_runtime  # noqa: E402


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    hook_runtime._reset_config_cache()
    yield tmp_path
    hook_runtime._reset_config_cache()


def _events(day=None):
    return deny_audit.read_deny_events(day)


# --- widen: SOFT-only, the hard floor has no appeal ---

def test_widen_soft_appends():
    assert deny_audit.main(["--request-widen", "docs/x.md", "--reason", "need docs"]) == 0
    evs = _events()
    assert any(e["event"] == "widen_request" and e["target"].endswith("docs/x.md") for e in evs)


def test_widen_core_refused():
    assert deny_audit.main(["--request-widen", "harness/hooks/x.py", "--reason", "x"]) != 0
    assert _events() == []  # refused -> nothing written


def test_widen_hard_binary_refused():
    assert deny_audit.main(["--request-widen", "harness/plugins/hs/x.py", "--reason", "x"]) != 0
    assert _events() == []


# --- chain continuity + tamper detection ---

def test_verify_chain_continuous():
    deny_audit.emit_hard_block(path="harness/hooks/a.py", session="s", reason="r")
    deny_audit.emit_soft_hit(path="docs/b.md", session="s", matched_rule="docs/**")
    deny_audit.emit_widen_request(path="docs/c.md", reason="r", session="s")
    assert deny_audit.verify_chain() is True
    assert len(_events()) == 3


def test_verify_chain_detects_tamper():
    for i in range(3):
        deny_audit.emit_hard_block(path="harness/hooks/%d.py" % i, session="s", reason="r")
    assert deny_audit.verify_chain() is True
    f = sorted(trace_log._trace_dir().glob("trace-*.jsonl"))[0]
    lines = f.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["target"] = "TAMPERED"  # change content, keep the stored chain_hash
    lines[1] = json.dumps(rec, ensure_ascii=False)
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert deny_audit.verify_chain() is False


# --- fail-open + record discipline + read filter ---

def test_emit_fail_open_on_write_error(monkeypatch):
    def _boom(**_):
        raise RuntimeError("trace unwritable")
    monkeypatch.setattr(trace_log, "append_event", _boom)
    deny_audit.emit_hard_block(path="x", session="s", reason="r")   # must not raise
    deny_audit.emit_soft_hit(path="x", session="s", matched_rule="y")  # must not raise


def test_events_carry_actor_and_ts():
    deny_audit.emit_hard_block(path="harness/hooks/a.py", session="s", reason="r")
    evs = _events()
    assert evs
    for e in evs:
        assert e.get("actor") and e.get("ts")


def test_read_filters_only_deny_events():
    trace_log.append_event(hook="other", event="some_other_event", session="s")
    deny_audit.emit_soft_hit(path="docs/x.md", session="s", matched_rule="docs/**")
    evs = _events()
    assert len(evs) == 1
    assert evs[0]["event"] == "deny_soft_hit"
