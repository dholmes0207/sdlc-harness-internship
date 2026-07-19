"""dev_override_wiring_nudge — thin turn-end wrapper: gated OFF by default,
surfaces the detector's signal via route_relay_nudge when enabled, and ALWAYS
allows (advisory, never blocks).
"""
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hook_runtime  # noqa: E402
import dev_override_wiring_nudge as nud  # noqa: E402


class _FakeDetector:
    def __init__(self, signal):
        self._signal = signal

    def collect(self, project_dir):
        return self._signal


def test_disabled_is_silent(monkeypatch):
    monkeypatch.setattr(nud, "_enabled", lambda: False)
    routed = []
    monkeypatch.setattr(hook_runtime, "route_relay_nudge",
                        lambda *a, **k: routed.append(a))
    assert nud.handle_stop({"cwd": "/tmp"}, "/tmp") == 0
    assert routed == []  # gated off → nothing surfaced


def test_enabled_unwired_routes_and_allows(monkeypatch):
    monkeypatch.setattr(nud, "_enabled", lambda: True)
    monkeypatch.setattr(nud, "_import_detector",
                        lambda: _FakeDetector({"unwired": ["partner.yaml"]}))
    captured = {}

    def _capture(name, text, record_obs, **k):
        captured["name"], captured["text"] = name, text
        record_obs()  # exercise the observation recorder

    monkeypatch.setattr(hook_runtime, "route_relay_nudge", _capture)
    rc = nud.handle_stop({"cwd": "/tmp", "session_id": "s1"}, "/tmp")
    assert rc == 0  # always allows
    assert captured["name"] == "dev_override_wiring_nudge"
    assert "partner.yaml" in captured["text"]
    assert "settings.local.json" in captured["text"]  # actionable fix named


def test_enabled_but_wired_is_silent(monkeypatch):
    monkeypatch.setattr(nud, "_enabled", lambda: True)
    monkeypatch.setattr(nud, "_import_detector", lambda: _FakeDetector(None))
    routed = []
    monkeypatch.setattr(hook_runtime, "route_relay_nudge",
                        lambda *a, **k: routed.append(a))
    assert nud.handle_stop({"cwd": "/tmp"}, "/tmp") == 0
    assert routed == []  # detector said None → nothing surfaced


def test_detector_import_failure_degrades_not_raises(monkeypatch):
    monkeypatch.setattr(nud, "_enabled", lambda: True)

    def _boom():
        raise ImportError("no detector")

    monkeypatch.setattr(nud, "_import_detector", _boom)
    # degraded path must still allow the turn, never raise
    assert nud.handle_stop({"cwd": "/tmp", "session_id": "s1"}, "/tmp") == 0


def test_advisory_text_lists_every_unwired_file():
    text = nud._advisory_text({"unwired": ["partner.yaml", "guard-policy.yaml"]})
    assert "partner.yaml" in text and "guard-policy.yaml" in text
    assert "HARNESS_" in text and "restart" in text.lower()
