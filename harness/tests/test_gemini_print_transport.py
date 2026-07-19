"""PrintTransport (agy -p) unit + engine-select through the chokepoint.

The real agy shape was probed live (`agy --model X -p "<prompt>"` -> clean stdout);
the fake mirrors it and is wired via HARNESS_AGY_CMD (the D30 seam). agy carries no
API key — the diagonal is agy => OAuth, so its stamp is engine=agy/transport=print/
auth=oauth, and token stats are n/a (agy reports none).
"""
import sys
from pathlib import Path

import pytest
import yaml

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import gemini_transport as gt  # noqa: E402
import gemini_companion as gc  # noqa: E402

_FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_agy.py"


def _agy_env(monkeypatch, *extra):
    cmd = ("python3 %s %s" % (_FAKE, " ".join(str(x) for x in extra))).strip()
    monkeypatch.setenv("HARNESS_AGY_CMD", cmd)


_BASE_AGY = {
    "master": "on", "mode": "partner", "write": "read_only",
    "stop_review_gate": "off", "engine": "agy-print",
    "purposes": {"research": "flash", "scout": "flash", "review": "pro",
                 "critique": "pro", "redteam": "pro", "delegate": "pro", "fix": "pro"},
    "route_all_surface": ["research", "scout"], "overrides": {},
    "timeouts": {"default": 30, "scout": 30}, "retry": {"max_attempts": 1, "on_markers": ["rate_limit"]},
    "secret_scrub": "warn",
}


def _cfg(tmp_path, **over):
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(yaml.safe_dump({**_BASE_AGY, **over}, sort_keys=False), encoding="utf-8")
    return p


# --- P3-T1: agy stdout -> content.text --------------------------------------
def test_p3t1_print_stdout_becomes_text(monkeypatch):
    _agy_env(monkeypatch)
    rr = gt.PrintTransport().run(composed="pong", mode="plan", session=None,
                                 cwd=None, timeout=30,
                                 model="Gemini 3.5 Flash (Medium)", engine_cfg={})
    assert rr.content["text"] == "pong"
    # round 1 recovers a conversation id from the log (P6) — so a caller CAN resume;
    # the fake writes a UUID v4, so session is that id (not None).
    assert rr.session is not None


# --- P3-T2: non-transient exit raises, not transient ------------------------
def test_p3t2_nonzero_exit_raises_nontransient(monkeypatch):
    _agy_env(monkeypatch, "--exit-code", 1, "--stderr-marker", "not_logged_in")
    with pytest.raises(gt.AcpError) as e:
        gt.PrintTransport().run(composed="x", mode="plan", session=None, cwd=None,
                                timeout=30, model="m", engine_cfg={})
    assert gc._is_transient(e.value, ["rate_limit"]) is False


# --- P3-T3: transient marker classified transient ---------------------------
def test_p3t3_transient_marker(monkeypatch):
    _agy_env(monkeypatch, "--exit-code", 1, "--stderr-marker", "rate_limit")
    with pytest.raises(gt.AcpError) as e:
        gt.PrintTransport().run(composed="x", mode="plan", session=None, cwd=None,
                                timeout=30, model="m", engine_cfg={})
    assert gc._is_transient(e.value, ["rate_limit"]) is True


# --- P3-T5: partner_call selects PrintTransport for agy-print ---------------
def test_p3t5_partner_call_uses_print_transport(tmp_path, monkeypatch):
    _agy_env(monkeypatch)
    out = gc.partner_call("review", "check this", config_path=_cfg(tmp_path))
    assert out.status == "ok"
    assert out.content["text"]  # agy answered (echoed composed)


# --- P3-T6: provenance stamp carries the agy diagonal (D32) -----------------
def test_p3t6_agy_provenance_diagonal(tmp_path, monkeypatch):
    _agy_env(monkeypatch)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path))
    p = out.provenance
    assert p["engine"] == "agy"
    assert p["transport"] == "print"
    assert p["auth"] == "oauth"
    assert p["reviewer_engine"] == "agy"
    assert p["reviewer_model"] == "Gemini 3.1 Pro (High)"  # pro tier default


# --- P3-T7: agy result has no token stats -> {} ("n/a") ---------------------
def test_p3t7_stats_na_for_agy(tmp_path, monkeypatch):
    _agy_env(monkeypatch)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path))
    assert gc._stats_of(out) == {}


# --- P3-T8: secret-scan still runs on the agy payload -----------------------
def test_p3t8_secret_scan_runs_on_agy(tmp_path, monkeypatch, capsys):
    _agy_env(monkeypatch)
    out = gc.partner_call("review", "export AWS_SECRET_KEY=AKIAIOSFODNN7EXAMPLE",
                          config_path=_cfg(tmp_path))
    assert "secret" in capsys.readouterr().err.lower()
    assert out.status == "ok"  # warn-only, still sent


# --- P5: PrintTransport strips SSH_* from agy's env --------------------------
def test_p5_print_transport_strips_ssh_env(monkeypatch):
    # SSH_* leaking into agy → file-token auth dies (probed). The transport must
    # strip them; the fake exits 1 if any SSH_* survives.
    monkeypatch.setenv("SSH_CLIENT", "10.0.0.1 22 22")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")
    _agy_env(monkeypatch, "--fail-on-ssh")
    rr = gt.PrintTransport().run(composed="ok", mode="plan", session=None, cwd=None,
                                 timeout=30, model="m", engine_cfg={})
    assert rr.content["text"] == "ok"  # ran clean → SSH_* was stripped
