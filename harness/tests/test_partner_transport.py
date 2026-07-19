"""CcsPrintTransport (ccs <provider> -p ... --output-format json) unit tests.

ccs's real stdout is polluted (banner ANSI art + a human table + three
newline-delimited JSON stream objects) — never a single clean JSON document
(probed live). fixtures/fake_ccs.py mirrors that exact shape
so a transport that (wrongly) called json.loads(stdout) fails HERE, before it
ever reaches a real ccs call (dogfood-beats-fake).
"""
import sys
from pathlib import Path

import pytest

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import partner_transport as pt  # noqa: E402

_FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_ccs.py"


def _ccs_env(monkeypatch, mode=None):
    monkeypatch.setenv("HARNESS_CCS_CMD", "%s %s" % (sys.executable, _FAKE))
    if mode is not None:
        monkeypatch.setenv("FAKE_CCS_MODE", mode)
    else:
        monkeypatch.delenv("FAKE_CCS_MODE", raising=False)


def _run(**over):
    base = dict(composed="hi", mode="plan", session=None, cwd=None, timeout=10,
                provider="foo")
    base.update(over)
    return pt.CcsPrintTransport().run(**base)


def test_parse_result_model_cost(monkeypatch):
    _ccs_env(monkeypatch)
    rr = _run()
    assert rr.content["text"] == "fake answer: hi"
    assert rr.content["model"] == "fake-model-1"
    assert rr.content["cost"] == pytest.approx(0.0042)
    assert rr.content["usage"] == {"input_tokens": 12, "output_tokens": 34}
    assert rr.session  # a uuid string from the system record, present


def test_banner_pollution_not_json_loads(monkeypatch):
    # fake_ccs ALWAYS emits banner ANSI art + a table BEFORE the JSON stream —
    # a stdout starting with something other than "{". A transport that called
    # json.loads(stdout) directly would raise json.JSONDecodeError here; this
    # only proves the real parser survives that pollution and still recovers
    # the result record.
    _ccs_env(monkeypatch)
    rr = _run(composed="ping")
    assert rr.content["text"] == "fake answer: ping"


def test_no_result_record_degrades(monkeypatch):
    _ccs_env(monkeypatch, mode="no_result")
    with pytest.raises(pt.PartnerError):
        _run()


def test_nonzero_exit_raises(monkeypatch):
    _ccs_env(monkeypatch, mode="nonzero")
    with pytest.raises(pt.PartnerError) as e:
        _run()
    assert "fake failure" in str(e.value)


def test_timeout_raises(monkeypatch):
    _ccs_env(monkeypatch, mode="timeout")
    with pytest.raises(pt.PartnerTimeout):
        _run(timeout=1)


def test_non_json_stdout_raises(monkeypatch):
    _ccs_env(monkeypatch, mode="garbage")
    with pytest.raises(pt.PartnerError):
        _run()


def test_missing_result_key_raises_not_empty_ok(monkeypatch):
    # A type=="result" record that IS present but carries no "result" text
    # field must degrade loud — never silently reported as an ok, empty
    # answer (that would look like a successful call that said nothing).
    _ccs_env(monkeypatch, mode="no_result_key")
    with pytest.raises(pt.PartnerError):
        _run()


def test_advisory_passes_plan_mode(monkeypatch):
    # fake_ccs echoes the received --permission-mode into the system record's
    # permissionMode field — assert the transport actually sent the flag for
    # an advisory (mode="plan") call, not just that a result came back.
    _ccs_env(monkeypatch)
    rr = _run(mode="plan")
    assert rr.content["permission_mode"] == "plan"
