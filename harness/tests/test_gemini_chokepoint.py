"""Chokepoint contract (phase 3): partner_call is the ONE code path to gemini.

Every call is stamped with provenance (engine+model); a down gemini degrades
LOUDLY with the stamp intact (never a silent Claude fallback, S4); secrets in the
prompt WARN but do not block (D7 v1); master=off is inert (no spawn, S6). The
gemini transport is injected by monkeypatching gemini_companion.GeminiPrintTransport
with a fake — no gemini, no network.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import gemini_companion as gc  # noqa: E402
import gemini_transport as gt  # noqa: E402
import resolve_model  # noqa: E402

_BASE = {
    "master": "on", "mode": "partner", "write": "read_only", "stop_review_gate": "off",
    # Pin the gemini lane so this suite is deterministic on ANY machine: `auto` would
    # env-detect (GEMINI_API_KEY -> gemini-print, else agy-print), and the agy branch
    # would bypass the _FakeTransport monkeypatch. conftest also scrubs the key (F2).
    "engine": "gemini-print",
    "purposes": {"research": "flash", "scout": "flash", "review": "pro",
                 "critique": "pro", "redteam": "pro", "delegate": "pro", "fix": "pro"},
    "route_all_surface": ["research", "scout"], "overrides": {},
    "timeouts": {"default": 5, "scout": 5}, "retry": {"max_attempts": 2, "on_markers": ["rate_limit"]},
    "secret_scrub": "warn",
}


def _cfg(tmp_path, **over):
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(yaml.safe_dump({**_BASE, **over}, sort_keys=False), encoding="utf-8")
    return p


class _FakeTransport:
    """Fakes GeminiPrintTransport.run: echoes the composed prompt back as the
    response text (so tests can assert what was sent) with print-shaped content;
    counts instantiations so an inert lane can assert it never spawned."""
    instances = 0

    def __init__(self):
        type(self).instances += 1

    def run(self, *, composed, mode, session, cwd, timeout, model, engine_cfg):
        return gt.RunResult(
            content={"text": composed,
                     "stats": {"models": {model: {"tokens": {"input": 10,
                                                             "total": 12}}}}},
            session=session or "sess-1")


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeTransport.instances = 0


# --- T1: provenance stamp carries engine + resolved model -------------------
def test_t1_result_carries_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    out = gc.partner_call("review", "check this", config_path=_cfg(tmp_path))
    assert out.status == "ok"
    assert out.provenance["reviewer_engine"] == "gemini"
    assert out.provenance["reviewer_model"] == resolve_model.resolve_model(tier="pro")


# --- T2: per-purpose override model wins ------------------------------------
def test_t2_override_model_used(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    out = gc.partner_call("review", "x",
                          config_path=_cfg(tmp_path, overrides={"review": "gemini-2.5-pro"}))
    assert out.provenance["reviewer_model"] == "gemini-2.5-pro"


# --- T3: secret in prompt WARNS but does not block --------------------------
def test_t3_secret_warns_not_blocked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    out = gc.partner_call("review", "export AWS_SECRET_KEY=AKIAIOSFODNN7EXAMPLE",
                          config_path=_cfg(tmp_path))
    err = capsys.readouterr().err
    assert "secret" in err.lower()
    assert out.status == "ok"  # still sent — v1 warn-only
    assert "AWS_SECRET_KEY" in out.content["text"]  # user content carried through


# --- T4: a down gemini degrades loudly, stamped, no Claude fallback ---------
def test_t4_degrade_is_stamped_and_loud(tmp_path, monkeypatch, capsys):
    class _Down(_FakeTransport):
        def run(self, **kw):
            raise gc.AcpError("connection refused")
    monkeypatch.setattr(gc, "GeminiPrintTransport", _Down)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path))
    assert out.status == "degraded"
    assert out.provenance["reviewer_engine"] == "gemini"
    assert "connection refused" in out.reason
    assert "DEGRADED" in capsys.readouterr().err


# --- T5: master off is inert (no spawn) -------------------------------------
def test_t5_master_off_inert(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path, master="off"))
    assert out.status == "inert"
    assert _FakeTransport.instances == 0  # never spawned


# --- T6: a transient marker triggers one retry, then succeeds ---------------
def test_t6_retry_on_transient(tmp_path, monkeypatch):
    calls = {"n": 0}

    class _Flaky(_FakeTransport):
        def run(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise gc.AcpError("rate_limit exceeded")
            return gt.RunResult(content={"text": kw["composed"]}, session="s")
    monkeypatch.setattr(gc, "GeminiPrintTransport", _Flaky)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path))
    assert out.status == "ok"
    assert calls["n"] == 2  # failed once, retried, succeeded


# --- T7b: the purpose template is prepended (load-bearing methodology) ------
def test_t7b_purpose_template_is_prepended(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    out = gc.partner_call("research", "compare option A vs B", config_path=_cfg(tmp_path))
    sent = out.content["text"]  # the fake echoes the composed prompt
    assert "trade-off matrix" in sent.lower()      # researcher methodology injected
    assert "--- TASK ---" in sent                  # user task delimited
    assert "compare option A vs B" in sent


def test_t7c_unknown_purpose_has_no_preamble(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    out = gc.partner_call("scout", "find X", config_path=_cfg(tmp_path))
    assert out.content["text"] == "find X"  # no template for scout → raw prompt


# --- T7e: a continuation carries the prior session id into the transport -----
def test_t7e_continuation_resumes_session(tmp_path, monkeypatch):
    calls = {"session": "unset"}

    class _Cont(_FakeTransport):
        def run(self, *, composed, mode, session, cwd, timeout, model, engine_cfg):
            calls["session"] = session  # the resume id the chokepoint passed down
            return gt.RunResult(content={"text": composed}, session=session)
    monkeypatch.setattr(gc, "GeminiPrintTransport", _Cont)
    out = gc.partner_call("review", "x", session="s-prev", config_path=_cfg(tmp_path))
    assert out.session == "s-prev"       # resumed id echoed back (no amnesia)
    assert calls["session"] == "s-prev"  # the prior session reached the transport


# --- T7d: token stats read from the real _meta.quota location ---------------
def test_t7d_stats_from_meta_quota():
    # Regression for the dogfood bug: real gemini puts token counts at
    # result._meta.quota.token_count, NOT a flat `stats`.
    class _R:
        content = {"stopReason": "end_turn",
                   "_meta": {"quota": {"token_count":
                             {"input_tokens": 500, "output_tokens": 42}}}}
    stats = gc._stats_of(_R())
    assert stats == {"input_tokens": 500, "output_tokens": 42}


def test_t7d_stats_scalar_token_count_no_crash():
    # wire drift: token_count as a scalar must degrade to {}, never AttributeError
    class _R:
        content = {"_meta": {"quota": {"token_count": 999}}}
    assert gc._stats_of(_R()) == {}


# --- print-mode token stats: stats.models.<model>.tokens (gemini -p -o json) --
def test_stats_of_print_shape():
    # gemini -p -o json reports tokens at stats.models.<model>.tokens (probed live);
    # normalize to the flat {input,total,thoughts}_tokens shape.
    class _R:
        content = {"stats": {"models": {"gemini-3.1-pro": {
            "tokens": {"input": 12819, "total": 13024, "thoughts": 204}}}}}
    assert gc._stats_of(_R()) == {"input_tokens": 12819, "total_tokens": 13024,
                                  "thoughts_tokens": 204}


def test_stats_of_print_multiple_models_summed():
    # More than one model in a single call → aggregate (sum) so no usage is dropped.
    class _R:
        content = {"stats": {"models": {
            "flash": {"tokens": {"input": 100, "total": 150, "thoughts": 10}},
            "pro": {"tokens": {"input": 200, "total": 260, "thoughts": 5}}}}}
    assert gc._stats_of(_R()) == {"input_tokens": 300, "total_tokens": 410,
                                  "thoughts_tokens": 15}


def test_stats_of_print_models_no_tokens_returns_empty():
    # A models blob with no usable tokens sub-dict → fail-open {} (never crash).
    class _R:
        content = {"stats": {"models": {"m": {"api": {"totalRequests": 1}}}}}
    assert gc._stats_of(_R()) == {}


def test_stats_of_print_explicit_null_token_no_crash():
    # wire drift: an explicit null token count must coalesce to 0, not crash with a
    # TypeError (int += None). tok.get("input", 0) returns None when the key EXISTS
    # with a null value — the default only fires on a MISSING key.
    class _R:
        content = {"stats": {"models": {"m": {"tokens": {"input": None,
                                                        "total": 13, "thoughts": None}}}}}
    assert gc._stats_of(_R()) == {"input_tokens": 0, "total_tokens": 13,
                                  "thoughts_tokens": 0}


def test_stats_of_legacy_flat_stats_back_compat():
    # A flat `stats` (no `models` key) still returns as-is until ACP is retired.
    class _R:
        content = {"stats": {"input_tokens": 7, "output_tokens": 3}}
    assert gc._stats_of(_R()) == {"input_tokens": 7, "output_tokens": 3}


def test_stats_of_unknown_shape_returns_empty():
    class _R:
        content = {"nothing": "here"}
    assert gc._stats_of(_R()) == {}


# --- T7: schema stays back-compat (reviewer_engine/model optional) ----------
def test_t7_schema_back_compat():
    schema = json.loads((Path(__file__).resolve().parent.parent
                         / "schemas" / "artifact-review-decision.json").read_text())
    props = schema["properties"]
    assert "reviewer_engine" in props and "reviewer_model" in props
    # required MUST NOT grow — an old review-decision omitting them stays valid.
    assert "reviewer_engine" not in schema["required"]
    assert "reviewer_model" not in schema["required"]
    assert schema["required"] == ["verdict", "reviewer", "role", "rationale"]


# --- Phase 4: cross-engine fallback (auto) + attempts[] + pin/write/session guards
_FAKE_AGY = Path(__file__).resolve().parent / "fixtures" / "fake_agy.py"


def _agy_ok(monkeypatch):
    monkeypatch.setenv("HARNESS_AGY_CMD", "python3 %s" % _FAKE_AGY)


def _agy_down(monkeypatch, marker="not_logged_in"):
    monkeypatch.setenv("HARNESS_AGY_CMD",
                       "python3 %s --exit-code 1 --stderr-marker %s" % (_FAKE_AGY, marker))


class _DownTransport(_FakeTransport):
    """gemini print run always fails — drives degrade/fallback/no-fallback tests
    (print has no ACP lifecycle, so every down case collapses to run() raising)."""
    def run(self, **kw):
        raise gc.AcpError("gemini connection refused")


def test_p4t1_auto_gemini_down_falls_back_to_agy(tmp_path, monkeypatch):
    # dummy key (conftest) → auto primary = gemini-print; gemini down → agy fallback
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    _agy_ok(monkeypatch)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path, engine="auto"))
    assert out.status == "ok"
    assert out.provenance["engine"] == "agy"
    trail = out.provenance["attempts"]
    assert [a["engine"] for a in trail] == ["gemini", "agy"]
    assert trail[0]["status"] == "failed" and trail[1]["status"] == "ok"


def test_p4t2_auto_agy_down_falls_back_to_gemini(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # no key → primary agy
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)     # gemini ok
    _agy_down(monkeypatch)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path, engine="auto"))
    assert out.status == "ok"
    assert out.provenance["engine"] == "gemini"
    assert [a["engine"] for a in out.provenance["attempts"]] == ["agy", "gemini"]


def test_p4t3_pin_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    _agy_ok(monkeypatch)  # agy WOULD work, but the pin forbids fallback (D7)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path, engine="gemini-print"))
    assert out.status == "degraded"
    assert [a["engine"] for a in out.provenance["attempts"]] == ["gemini"]


def test_p4t4_write_mode_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    _agy_ok(monkeypatch)
    out = gc.partner_call("delegate", "x", mode="write",
                          config_path=_cfg(tmp_path, engine="auto"))
    assert out.status == "degraded"
    assert [a["engine"] for a in out.provenance["attempts"]] == ["gemini"]


def test_p4t4b_yolo_mode_no_fallback(tmp_path, monkeypatch):
    # the guard is an allowlist {yolo,write} — "yolo" must not slip through (F8)
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    _agy_ok(monkeypatch)
    out = gc.partner_call("delegate", "x", mode="yolo",
                          config_path=_cfg(tmp_path, engine="auto"))
    assert out.status == "degraded"
    assert [a["engine"] for a in out.provenance["attempts"]] == ["gemini"]


def test_p4t5_both_down_degrades_loud(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    _agy_down(monkeypatch)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path, engine="auto"))
    assert out.status == "degraded"
    trail = out.provenance["attempts"]
    assert [a["engine"] for a in trail] == ["gemini", "agy"]
    assert all(a["status"] == "failed" for a in trail)
    assert "DEGRADED" in capsys.readouterr().err


def test_p4t6_winner_domain_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    _agy_ok(monkeypatch)
    out = gc.partner_call("review", "x", config_path=_cfg(tmp_path, engine="auto"))
    assert out.provenance["engine"] in ("gemini", "agy")  # never Claude (D11)


def test_p4t7_run_job_appends_per_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    _agy_ok(monkeypatch)
    reg = gc.JobRegistry(state_dir=str(tmp_path / "state"))
    cfgp = _cfg(tmp_path, engine="auto")
    gc._run_job(reg, "review", "review", "x", "plan", str(cfgp))
    attempt_recs = [r for r in reg.read_all() if r.get("status") == "attempt"]
    assert [r["engine"] for r in attempt_recs] == ["gemini", "agy"]  # append-only trail


def test_p4t8_winner_stamp_is_winning_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)  # gemini down → agy wins
    _agy_ok(monkeypatch)
    p = gc.partner_call("review", "x", config_path=_cfg(tmp_path, engine="auto")).provenance
    assert (p["engine"], p["transport"], p["auth"]) == ("agy", "print", "oauth")
    assert p["reviewer_model"] == "Gemini 3.1 Pro (High)"  # agy pro, NOT the gemini model


def test_p4t9_session_set_no_fallback(tmp_path, monkeypatch):
    # a continuation carries a session-id that belongs to ONE engine; never inject
    # it into the other. gemini down + session set → no fallback, degrade loud.
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    _agy_ok(monkeypatch)
    out = gc.partner_call("review", "x", session="sess-prev",
                          config_path=_cfg(tmp_path, engine="auto"))
    assert out.status == "degraded"
    assert [a["engine"] for a in out.provenance["attempts"]] == ["gemini"]


def test_p4_engine_pin_arg_overrides_auto(tmp_path, monkeypatch):
    # the --engine arg pins even when the config says auto (D7)
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    _agy_ok(monkeypatch)
    out = gc.partner_call("review", "x", engine="gemini-print",
                          config_path=_cfg(tmp_path, engine="auto"))
    assert out.status == "degraded"
    assert [a["engine"] for a in out.provenance["attempts"]] == ["gemini"]


def test_p7_m1_arg_pin_skips_inert_auth(tmp_path, monkeypatch):
    # review M-1: an arg-level --engine pin is ATTEMPTED even when the config (auto)
    # would be inert-auth — never short-circuited with a misleading "engine=auto".
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("HARNESS_AGY_HOME", str(tmp_path / "no-agy"))  # no agy login
    monkeypatch.delenv("HARNESS_AGY_CMD", raising=False)
    monkeypatch.setattr(gc, "GeminiPrintTransport", _FakeTransport)
    out = gc.partner_call("review", "x", engine="gemini-print",
                          config_path=_cfg(tmp_path, engine="auto"))
    assert out.status == "ok"                       # pinned engine attempted, not inert
    assert out.provenance["engine"] == "gemini"
