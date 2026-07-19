"""notify_remote — opt-in Notification relay to a user webhook.

Ships OFF (no webhook -> no-op). When configured it POSTs only the notification text
to an https URL whose host resolves to a globally-routable address, fail-open, and
never leaks the webhook. These tests pin that contract without a real network call
(the post is monkeypatched; DNS resolution is monkeypatched per case).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "hooks"))
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import notify_remote as nr  # noqa: E402

_PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]


def _resolve_to(monkeypatch, ip):
    monkeypatch.setattr("socket.getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", (ip, 443))])


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(nr, "_post", lambda url, payload: calls.append((url, payload)))
    return calls


def test_no_webhook_is_a_noop(monkeypatch):
    monkeypatch.delenv("HARNESS_NOTIFY_WEBHOOK", raising=False)
    calls = _capture(monkeypatch)
    nr.core({"message": "needs you"})
    assert calls == []


def test_public_https_webhook_relays_message(monkeypatch):
    monkeypatch.setenv("HARNESS_NOTIFY_WEBHOOK", "https://hooks.example.com/abc")
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: _PUBLIC)
    calls = _capture(monkeypatch)
    nr.core({"message": "  goal hit a decision  "})
    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "https://hooks.example.com/abc"
    assert payload == {"text": "goal hit a decision"}  # trimmed


def test_dns_resolving_to_internal_is_rejected(monkeypatch):
    # SSRF: a DNS name resolving to a private / metadata address must be rejected
    calls = _capture(monkeypatch)
    for ip in ("10.0.0.5", "192.168.1.9", "169.254.169.254", "172.16.0.1"):
        monkeypatch.setenv("HARNESS_NOTIFY_WEBHOOK", "https://evil.example.com/x")
        _resolve_to(monkeypatch, ip)
        nr.core({"message": "x"})
    assert calls == []


def test_scheme_and_loopback_rejected(monkeypatch):
    # http / non-https rejected before resolution; loopback literals resolve to a
    # non-global address and are rejected too (real resolution, no mock).
    calls = _capture(monkeypatch)
    for bad in ("http://hooks.example.com/x", "ftp://x/y", "",
                "https://127.0.0.1/x", "https://[::1]/x"):
        monkeypatch.setenv("HARNESS_NOTIFY_WEBHOOK", bad)
        nr.core({"message": "x"})
    assert calls == []


def test_unresolvable_host_fails_closed(monkeypatch):
    monkeypatch.setenv("HARNESS_NOTIFY_WEBHOOK", "https://nope.invalid/x")

    def boom(*a, **k):
        raise OSError("name resolution failed")

    monkeypatch.setattr("socket.getaddrinfo", boom)
    calls = _capture(monkeypatch)
    nr.core({"message": "x"})
    assert calls == []  # cannot verify the host -> do not send


def test_empty_message_is_a_noop(monkeypatch):
    monkeypatch.setenv("HARNESS_NOTIFY_WEBHOOK", "https://hooks.example.com/abc")
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: _PUBLIC)
    calls = _capture(monkeypatch)
    nr.core({"message": "   "})
    nr.core({})
    nr.core({"message": None})
    assert calls == []


def test_message_truncated(monkeypatch):
    monkeypatch.setenv("HARNESS_NOTIFY_WEBHOOK", "https://hooks.example.com/abc")
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: _PUBLIC)
    calls = _capture(monkeypatch)
    nr.core({"message": "x" * 5000})
    assert len(calls[0][1]["text"]) == nr._MAX


def test_post_error_is_swallowed(monkeypatch):
    monkeypatch.setenv("HARNESS_NOTIFY_WEBHOOK", "https://hooks.example.com/abc")
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: _PUBLIC)

    def boom(url, payload):
        raise OSError("network down")

    monkeypatch.setattr(nr, "_post", boom)
    nr.core({"message": "x"})  # must not raise — fail-open


def test_malformed_idna_host_fails_closed_no_exception(monkeypatch):
    # a host with a DNS label >63 chars makes getaddrinfo raise UnicodeError (not an
    # OSError) — the guard must catch it and fail closed (no send), not leak it.
    monkeypatch.setenv("HARNESS_NOTIFY_WEBHOOK", "https://nope.invalid/x")

    def boom(*a, **k):
        raise UnicodeError("'idna' codec failed: label too long")

    monkeypatch.setattr("socket.getaddrinfo", boom)
    calls = _capture(monkeypatch)
    nr.core({"message": "x"})  # must not raise
    assert calls == []
