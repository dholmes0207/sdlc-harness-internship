"""GeminiPrintTransport (`gemini -p … -o json`) unit tests.

Shape locked to a live probe against gemini 0.49.0: `-o json` returns
{session_id, response, stats.models.<model>.tokens{...}}, self-exits, no resident
server. The fake mirrors it and is wired via HARNESS_GEMINI_PRINT_CMD (the seam
that mirrors HARNESS_AGY_CMD). Same RunResult contract as PrintTransport so
partner_call never branches on the engine.
"""
import os
import sys
from pathlib import Path

import pytest

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import gemini_transport as gt  # noqa: E402
import gemini_companion as gc  # noqa: E402

_FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_gemini_print.py"


def _print_env(monkeypatch, *extra):
    cmd = ("python3 %s %s" % (_FAKE, " ".join(str(x) for x in extra))).strip()
    monkeypatch.setenv("HARNESS_GEMINI_PRINT_CMD", cmd)


def test_gemini_print_run_returns_text_and_session(monkeypatch):
    _print_env(monkeypatch)
    rr = gt.GeminiPrintTransport().run(composed="OK", mode="plan", session=None,
                                       cwd=None, timeout=30, model="gemini-3.1-pro",
                                       engine_cfg={})
    assert rr.content["text"] == "OK"
    # content keeps the raw json so P2 _stats_of can read stats.models.<model>.tokens
    assert rr.content["stats"]["models"]["gemini-3.1-pro"]["tokens"]["total"] == 16135
    assert rr.session  # session_id echoed from the fake


def test_gemini_print_advisory_is_plan_mode(monkeypatch):
    _print_env(monkeypatch)
    cmd_plan = gt._gemini_print_cmd("m", "plan", None)
    assert "plan" in cmd_plan and "--approval-mode" in cmd_plan
    for wmode in ("yolo", "write"):
        cmd_w = gt._gemini_print_cmd("m", wmode, None)
        assert "yolo" in cmd_w
        assert "plan" not in cmd_w


def test_gemini_print_nonzero_raises_acperror(monkeypatch):
    _print_env(monkeypatch, "--exit-code", 1, "--stderr-marker", "rate_limit")
    with pytest.raises(gt.AcpError) as e:
        gt.GeminiPrintTransport().run(composed="x", mode="plan", session=None,
                                      cwd=None, timeout=30, model="m", engine_cfg={})
    # stderr embedded so partner_call._is_transient can classify
    assert gc._is_transient(e.value, ["rate_limit"]) is True


def test_gemini_print_fatal_marker_not_transient(monkeypatch):
    _print_env(monkeypatch, "--exit-code", 1, "--stderr-marker", "auth failed")
    with pytest.raises(gt.AcpError) as e:
        gt.GeminiPrintTransport().run(composed="x", mode="plan", session=None,
                                      cwd=None, timeout=30, model="m", engine_cfg={})
    assert gc._is_transient(e.value, ["rate_limit"]) is False


def test_gemini_print_timeout_raises_acptimeout(monkeypatch):
    _print_env(monkeypatch, "--sleep", 2)
    with pytest.raises(gt.AcpTimeout):
        gt.GeminiPrintTransport().run(composed="x", mode="plan", session=None,
                                      cwd=None, timeout=1, model="m", engine_cfg={})


def test_gemini_print_bad_json_raises_acperror(monkeypatch):
    _print_env(monkeypatch, "--bad-json")
    with pytest.raises(gt.AcpError):
        gt.GeminiPrintTransport().run(composed="x", mode="plan", session=None,
                                      cwd=None, timeout=30, model="m", engine_cfg={})


@pytest.mark.parametrize("payload", ["null", "42", "3.14", "[1,2]", "true"])
def test_gemini_print_nonobject_json_raises_acperror(monkeypatch, payload):
    # Valid JSON that is not an object parses cleanly, so json.loads does NOT raise —
    # dict(payload) would then leak a raw TypeError/ValueError past the class's
    # AcpError/AcpTimeout contract (partner_call could not classify it). The transport
    # must map a non-dict payload to AcpError like any other malformed stdout.
    _print_env(monkeypatch, "--nonobject-json", payload)
    with pytest.raises(gt.AcpError):
        gt.GeminiPrintTransport().run(composed="x", mode="plan", session=None,
                                      cwd=None, timeout=30, model="m", engine_cfg={})


def test_gemini_print_session_resume_passes_flag(monkeypatch):
    _print_env(monkeypatch)
    # resume path: an existing session id → --resume <id> (live-probed mechanic)
    cmd = gt._gemini_print_cmd("m", "plan", "s1")
    assert "--resume" in cmd and "s1" in cmd
    rr = gt.GeminiPrintTransport().run(composed="x", mode="plan", session="s1",
                                       cwd=None, timeout=30, model="m", engine_cfg={})
    assert rr.session == "s1"  # fake echoes the resumed id — no amnesia


def test_gemini_print_cmd_override_env(monkeypatch):
    monkeypatch.setenv("HARNESS_GEMINI_PRINT_CMD", "python3 /some/fake.py --flag")
    cmd = gt._gemini_print_cmd("m", "plan", None)
    assert cmd[:3] == ["python3", "/some/fake.py", "--flag"]


# --- ACP retirement (P6): no gemini_acp module, error types have a new home ---
def test_no_acp_module_imported():
    # the retired ACP module must not be imported by the live transport/chokepoint
    src_transport = (Path(gt.__file__)).read_text(encoding="utf-8")
    src_companion = (Path(gc.__file__)).read_text(encoding="utf-8")
    assert "import gemini_acp" not in src_transport
    assert "from gemini_acp" not in src_transport
    assert "import gemini_acp" not in src_companion
    assert "from gemini_acp" not in src_companion


def test_error_types_available_from_transport_home():
    # AcpError/AcpTimeout now live in gemini_transport (ACP module deleted); the
    # chokepoint's retry/degrade except-clause must still resolve them.
    from gemini_transport import AcpError, AcpTimeout
    assert issubclass(AcpError, Exception) and issubclass(AcpTimeout, Exception)
    assert gc.AcpError is AcpError and gc.AcpTimeout is AcpTimeout


def test_gemini_acp_module_gone():
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("gemini_acp")


@pytest.mark.real_gemini
def test_t7_real_gemini_print_handshake():
    # Live opt-in: drive the REAL gemini print transport (no fake seam) and confirm
    # a `gemini -p "ok" -o json` handshake answers + parses. Deselected by default.
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("no GEMINI_API_KEY — live lane opt-in")
    rr = gt.GeminiPrintTransport().run(composed="Reply with just OK.", mode="plan",
                                       session=None, cwd=None, timeout=90, model="",
                                       engine_cfg={})
    assert rr.content.get("text")  # a real answer came back
    assert rr.session  # gemini returned a session id
