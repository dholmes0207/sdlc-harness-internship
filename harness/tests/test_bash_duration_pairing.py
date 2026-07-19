"""test_bash_duration_pairing.py — PreToolUse:Bash (mark_bash_start) +
PostToolUse:Bash (track_script_execution) duration pairing.

Covers: Pre stamps a start mark + Post computes `ms` (int >= 0) and clears the
mark; missing Pre -> record without `ms` (graceful degrade); a different command
does not consume another's mark; non-harness-script commands create no mark / no
record; pytest/disabled silence.

Harness re-home of the source pairing suite: sinks live under
HARNESS_STATE_DIR/telemetry (not CK_TELEMETRY_DIR); the script matcher targets
harness/scripts/*.py and harness/e2e/*.py (no skill-tree path-shape); hooks
import from harness/hooks + harness/scripts.
"""
import importlib
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _d in (str(_SCRIPTS), str(_HOOKS)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

# A real harness script reached through an interpreter — matches the
# execution-position matcher; a bare git/ls/grep never does.
SKILL_CMD = "python3 harness/scripts/verify_install.py --root ."


def _reload(tmp_path, monkeypatch, extra=None):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    monkeypatch.setenv("HARNESS_USER", "alice")  # hermetic actor (no git shell-out)
    for k, v in (extra or {}).items():
        monkeypatch.setenv(k, v)
    # Reload telemetry_paths IN PLACE (do not pop it): a fresh re-import would
    # orphan the canonical module object other test files hold, breaking their
    # own importlib.reload. reload() re-reads import-time env without swapping id.
    for m in ("hook_runtime", "mark_bash_start", "track_script_execution"):
        sys.modules.pop(m, None)
    import telemetry_paths, mark_bash_start, track_script_execution  # noqa
    importlib.reload(telemetry_paths)
    importlib.reload(mark_bash_start)
    importlib.reload(track_script_execution)
    monkeypatch.setattr(mark_bash_start.sys.stdout, "write", lambda _s: None)
    monkeypatch.setattr(track_script_execution.sys.stdout, "write", lambda _s: None)
    return telemetry_paths, mark_bash_start, track_script_execution


def _lines(tmp_path, name="hook-telemetry.jsonl"):
    # Union legacy sink + per-session shards (P2 shards session records to
    # `<stem>-<disc>.jsonl`) so the pairing assertions see every record.
    d = tmp_path / "state" / "telemetry"
    if not d.exists():
        return []
    stem, dot, ext = name.rpartition(".")
    pats = [name] if not dot else [name, "%s-*.%s" % (stem, ext)]
    out = []
    for pat in pats:
        for p in sorted(d.glob(pat)):
            if p.name.endswith(".bak"):
                continue
            out.extend(json.loads(l) for l in p.read_text().splitlines() if l.strip())
    return out


def _pre_payload(cmd, session="s1"):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": session})


def _post_payload(cmd, session="s1", resp=None):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd},
                       "tool_response": resp or {}, "session_id": session})


class TestPairing:
    def test_pre_then_post_yields_ms(self, tmp_path, monkeypatch):
        _tp, pre, post = _reload(tmp_path, monkeypatch)
        pre.main(_pre_payload(SKILL_CMD))
        post.main(_post_payload(SKILL_CMD))
        recs = _lines(tmp_path)
        assert len(recs) == 1
        assert "ms" in recs[0]
        assert isinstance(recs[0]["ms"], int) and recs[0]["ms"] >= 0
        assert recs[0]["exit"] == 0

    def test_mark_is_cleared_after_post(self, tmp_path, monkeypatch):
        tpmod, pre, post = _reload(tmp_path, monkeypatch)
        pre.main(_pre_payload(SKILL_CMD))
        # The .bashtimers marker exists after Pre ...
        timer_dir = tpmod.telemetry_dir() / ".bashtimers"
        assert any(timer_dir.iterdir())
        post.main(_post_payload(SKILL_CMD))
        # ... and is consumed (cleared) after Post.
        assert not any(timer_dir.iterdir())

    def test_missing_pre_degrades_without_ms(self, tmp_path, monkeypatch):
        _tp, _pre, post = _reload(tmp_path, monkeypatch)
        post.main(_post_payload(SKILL_CMD))
        recs = _lines(tmp_path)
        assert len(recs) == 1
        assert "ms" not in recs[0]

    def test_different_command_does_not_pair(self, tmp_path, monkeypatch):
        _tp, pre, post = _reload(tmp_path, monkeypatch)
        pre.main(_pre_payload(SKILL_CMD))
        other = "cd /repo && python3 harness/e2e/run_vertical_slice.py"
        post.main(_post_payload(other))
        recs = _lines(tmp_path)
        assert len(recs) == 1
        assert "ms" not in recs[0], "a different command must not consume another's mark"


class TestFilterAndSilence:
    def test_non_script_command_no_mark_no_record(self, tmp_path, monkeypatch):
        tpmod, pre, post = _reload(tmp_path, monkeypatch)
        pre.main(_pre_payload("git status"))
        post.main(_post_payload("git status"))
        assert _lines(tmp_path) == []
        assert not (tpmod.telemetry_dir() / ".bashtimers").exists() or \
            not any((tpmod.telemetry_dir() / ".bashtimers").iterdir())

    def test_disabled_no_writes(self, tmp_path, monkeypatch):
        _tp, pre, post = _reload(tmp_path, monkeypatch, extra={"HARNESS_TELEMETRY_DISABLED": "1"})
        pre.main(_pre_payload(SKILL_CMD))
        post.main(_post_payload(SKILL_CMD))
        assert _lines(tmp_path) == []
