"""test_skip_trace_diet.py — a disabled compliance gate records its skip ONCE per
(session, hook), not once per call.

compliance_skip_or_run wrote a `<skip_event>` audit line every time a disabled
guard fired. scout_block_guard (ships OFF) matches a wide matcher, so that was
~8.8k skip lines a day — 68% of the trace. The decision to skip is still
auditable from a single line per session (plus the git diff of the config that
disabled it), so a per-(session, hook) marker collapses the volume while keeping
the audit. Posture is unchanged: a skip still emits continue and exits 0.
"""
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hook_runtime as hr  # noqa: E402
import pytest  # noqa: E402


def _skip_records(state_dir, event, session):
    trace = Path(state_dir) / "trace"
    n = 0
    for f in trace.glob("trace-*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("event") == event and rec.get("session") == session:
                n += 1
    return n


def _call_skip(monkeypatch, session_id, name="scout_block_guard",
               event="scout_block_skip"):
    monkeypatch.setattr(hr, "read_stdin_json", lambda: {"session_id": session_id})
    monkeypatch.setattr(hr, "hook_enabled", lambda n, c: False)
    with pytest.raises(SystemExit) as ei:
        hr.compliance_skip_or_run(name, lambda d: None, skip_event=event)
    return ei.value.code


class TestSkipTraceDiet:
    def test_skip_trace_once_per_session(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        for _ in range(20):
            code = _call_skip(monkeypatch, "S1")
            assert code == 0
        assert _skip_records(tmp_path, "scout_block_skip", "S1") == 1

    def test_skip_trace_per_session_isolated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        for _ in range(5):
            _call_skip(monkeypatch, "A")
        for _ in range(5):
            _call_skip(monkeypatch, "B")
        assert _skip_records(tmp_path, "scout_block_skip", "A") == 1
        assert _skip_records(tmp_path, "scout_block_skip", "B") == 1

    def test_skip_still_continues(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        code = _call_skip(monkeypatch, "S1")
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out.get("continue") is True

    def test_enabled_path_delegates_no_marker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        monkeypatch.setattr(hr, "read_stdin_json", lambda: {"session_id": "S1"})
        monkeypatch.setattr(hr, "hook_enabled", lambda n, c: True)
        seen = {}
        monkeypatch.setattr(hr, "run_compliance_hook",
                            lambda name, core, **k: seen.update(name=name, data=k.get("data")))
        hr.compliance_skip_or_run("scout_block_guard", lambda d: None,
                                  skip_event="scout_block_skip")
        assert seen.get("name") == "scout_block_guard"
        # no skip marker written on the enabled path
        assert not (tmp_path / "skip-marks").exists() or \
            not any((tmp_path / "skip-marks").iterdir())

    def test_marker_failopen_still_continues(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        # an unwritable marker dir (here: the path is occupied by a plain file, so
        # mkdir/write fail) must still continue + exit 0 and never raise out — the
        # skip degrades to per-call recording, not to a blocked session.
        (tmp_path / "skip-marks").write_text("occupied", encoding="utf-8")
        code = _call_skip(monkeypatch, "S1")
        assert code == 0
        # degraded (no durable marker) -> a second call re-records rather than skips
        code2 = _call_skip(monkeypatch, "S1")
        assert code2 == 0
        assert _skip_records(tmp_path, "scout_block_skip", "S1") == 2
