"""test_victorialogs_ctl.py — the opt-in, default-OFF VictoriaLogs controller.

VictoriaLogs is an OPTIONAL log sink: TnGT off by default, self-contained without it
(verify_install never requires it, no hook imports it). The controller manages the
binary (start/stop/status) and pushes JSONL to /insert/jsonline. CI never runs the
real binary — a stub on PATH exercises the state machine; the push shape is asserted
against a mocked HTTP call.
"""
import json
import os
import stat
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import victorialogs_ctl as vl  # noqa: E402


def _stub_binary(tmp_path):
    """A long-running stub 'victoria-logs' on PATH (sleeps until killed)."""
    b = tmp_path / "bin"
    b.mkdir()
    p = b / "victoria-logs"
    p.write_text("#!/usr/bin/env python3\nimport time\nwhile True: time.sleep(1)\n",
                 encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return b


class TestDefaultOff:
    def test_status_off_no_spawn(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        monkeypatch.delenv("VICTORIALOGS_BIN", raising=False)
        assert vl.status() == "off"

    def test_not_required_by_verify_install(self):
        import verify_install
        src = Path(verify_install.__file__).read_text(encoding="utf-8")
        assert "victoria" not in src.lower(), "verify_install must not require VictoriaLogs"


class TestStateMachine:
    def test_start_status_stop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        binroot = _stub_binary(tmp_path)
        monkeypatch.setenv("VICTORIALOGS_BIN", str(binroot / "victoria-logs"))
        assert vl.start() == 0
        # give the stub a moment to be live
        for _ in range(20):
            if vl.status() == "running":
                break
            time.sleep(0.05)
        assert vl.status() == "running"
        assert vl.stop() == 0
        assert vl.status() == "off"

    def test_missing_binary_graceful(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("VICTORIALOGS_BIN", str(tmp_path / "does-not-exist"))
        rc = vl.start()
        assert rc != 0  # reports, non-zero, no traceback (caught below by pytest not raising)


class TestPush:
    def test_push_jsonline_shape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        captured = {}

        class _Resp:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b""

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["data"] = req.data
            captured["ctype"] = req.headers.get("Content-type")
            return _Resp()

        monkeypatch.setattr(vl.urllib.request, "urlopen", fake_urlopen)
        rc = vl.push([{"event": "core_timing", "hook": "gate_stage", "elapsed_ms": 3}],
                     port=9428)
        assert rc == 0
        assert "/insert/jsonline" in captured["url"]
        # body is newline-delimited JSON objects
        lines = captured["data"].decode("utf-8").strip().split("\n")
        assert json.loads(lines[0])["hook"] == "gate_stage"


class TestNoHotPath:
    def test_no_hook_imports_victorialogs(self):
        hooks = Path(__file__).resolve().parent.parent / "hooks"
        for p in hooks.glob("*.py"):
            assert "victorialogs_ctl" not in p.read_text(encoding="utf-8"), \
                "%s imports the opt-in sink — it must stay off the hot path" % p.name
