"""Claude-driven loop: round_n registry + loop config block + pattern invariant (phase 4).

The loop is Claude-driven (spawn a fresh relayer each round + feed the delta) — NOT
a Stop-hook autopilot and NOT a gemini self-drive. round_n is an append-only field
on the job registry; the loop config block (max_rounds/default_mode) is validated
fail-closed; the reference pattern must disclaim Stop-hook wiring and keep the
mechanical (hs:loop) modes separate from the judge (Claude-driven) mode.
"""
import sys
from pathlib import Path

import pytest
import yaml

_HARNESS = Path(__file__).resolve().parent.parent
for _p in (_HARNESS / "plugins" / "hs" / "scripts", _HARNESS / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import gemini_companion as gc  # noqa: E402
import gemini_partner_config as cfgmod  # noqa: E402

_PATTERN = (_HARNESS / "plugins" / "hs" / "skills" / "gemini" / "references"
            / "loop-pattern.md")

_BASE = {
    "master": "on", "mode": "partner", "write": "read_only", "stop_review_gate": "off",
    "purposes": {"research": "flash", "review": "pro"},
    "route_all_surface": [], "overrides": {},
    "timeouts": {"default": 5},
}


def _cfg_file(tmp_path, **over):
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(yaml.safe_dump({**_BASE, **over}, sort_keys=False), encoding="utf-8")
    return str(p)


# --- round_n registry -------------------------------------------------------
def test_registry_records_round_n(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    reg = gc.JobRegistry()
    reg.append({"job_id": "j1", "status": "running", "round_n": 2})
    reg.append({"job_id": "j2", "status": "running"})  # no round_n → no crash
    recs = reg.read_all()
    assert any(r.get("round_n") == 2 for r in recs)
    assert any("round_n" not in r for r in recs)


def test_run_job_stamps_round_n(tmp_path, monkeypatch):
    # gemini routes through GeminiPrintTransport → the conftest global fake answers.
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    cfg_path = _cfg_file(tmp_path)
    reg = gc.JobRegistry()
    gc._run_job(reg, "research", "research", "q", "plan", cfg_path, round_n=3)
    assert any(r.get("round_n") == 3 for r in reg.read_all())


# --- loop config block ------------------------------------------------------
def test_config_loop_defaults(tmp_path):
    # explicit block honored
    cfg = cfgmod.resolve(_cfg_file(tmp_path, loop={"max_rounds": 5, "default_mode": "judge"}))
    assert cfg["loop"]["max_rounds"] == 5
    assert cfg["loop"]["default_mode"] == "judge"
    # absent block → safe default
    cfg2 = cfgmod.resolve(_cfg_file(tmp_path))
    assert cfg2["loop"]["max_rounds"] == 3
    assert cfg2["loop"]["default_mode"] == "converge"


def test_config_loop_bad_mode_fails_closed(tmp_path):
    with pytest.raises(SystemExit):
        cfgmod.resolve(_cfg_file(tmp_path, loop={"max_rounds": 3, "default_mode": "bogus"}))


@pytest.mark.parametrize("bad", [0, -1])
def test_config_loop_bad_max_rounds_fails_closed(tmp_path, bad):
    with pytest.raises(SystemExit):
        cfgmod.resolve(_cfg_file(tmp_path, loop={"max_rounds": bad, "default_mode": "converge"}))


# --- reference pattern invariants -------------------------------------------
def test_pattern_exists():
    assert _PATTERN.is_file()


def test_pattern_no_stop_hook():
    text = _PATTERN.read_text(encoding="utf-8").lower()
    assert "stop-hook" in text or "stop hook" in text  # it must DISCUSS the ban
    # and must never instruct wiring one
    assert "additionalcontext" not in text or "not" in text
    assert "do not" in text or "never" in text


def test_pattern_separates_hs_loop_from_judge():
    text = _PATTERN.read_text(encoding="utf-8").lower()
    assert "hs:loop" in text and "judge" in text
    assert "converge" in text and "target" in text


def test_pattern_within_ref_cap():
    # check_skill_structure migrated the reference-size gate from LINES to CHARS
    # (the real token/context cost). Measure the whole file in chars against MAX_REF_CHARS.
    import check_skill_structure as css
    n = len(_PATTERN.read_text(encoding="utf-8"))
    assert n <= css.MAX_REF_CHARS, "loop-pattern.md is %d chars (max %d)" % (n, css.MAX_REF_CHARS)


# --- Phase 6: agy loop via --log-file conversation-id capture ----------------
import re  # noqa: E402

import gemini_transport as gt  # noqa: E402

_FAKE_AGY = Path(__file__).resolve().parent / "fixtures" / "fake_agy.py"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _agy_env(monkeypatch, *extra):
    cmd = ("python3 %s %s" % (_FAKE_AGY, " ".join(str(x) for x in extra))).strip()
    monkeypatch.setenv("HARNESS_AGY_CMD", cmd)


def _run(session=None, timeout=30):
    return gt.PrintTransport().run(composed="x", mode="plan", session=session,
                                   cwd=None, timeout=timeout, model="m", engine_cfg={})


# --- P6-T1: round 1 recovers the conversation UUID from the log -------------
def test_p6t1_round1_captures_uuid(monkeypatch):
    _agy_env(monkeypatch)
    rr = _run(session=None)
    assert _UUID_RE.match(rr.session or "")  # a real UUID recovered from the log


# --- P6-T2: a resume passes --conversation <uuid> ---------------------------
def test_p6t2_resume_passes_conversation(monkeypatch):
    captured = {}
    real_run = gt.subprocess.run

    def spy(cmd, **kw):
        captured["cmd"] = list(cmd)
        return real_run(cmd, **kw)
    monkeypatch.setattr(gt.subprocess, "run", spy)
    _agy_env(monkeypatch)
    uid = "a3907c80-1111-4222-8333-444455556666"
    _run(session=uid)
    assert "--conversation" in captured["cmd"]
    assert uid in captured["cmd"]


# --- P6-T3: round 1 with no id in the log → session None + WARN, no raise ----
def test_p6t3_recall_miss_degrades_one_shot(monkeypatch, capsys):
    _agy_env(monkeypatch, "--no-uuid-log")
    rr = _run(session=None)  # must NOT raise
    assert rr.session is None
    assert "one-shot" in capsys.readouterr().err.lower()


# --- P6-T3b: mid-loop (session set) + agy rejects → raise, never a silent fresh
def test_p6t3b_midloop_rejection_raises(monkeypatch):
    _agy_env(monkeypatch, "--exit-code", 1, "--stderr-marker", "unknown_conversation")
    with pytest.raises(gt.AcpError):
        _run(session="a3907c80-1111-4222-8333-444455556666")


# --- P6-T4: separate per-call logs → distinct ids (race-free) ---------------
def test_p6t4_distinct_ids_per_call(monkeypatch):
    _agy_env(monkeypatch)
    assert _run(session=None).session != _run(session=None).session


# --- P6-T5: partner_call(session=uuid) on agy resumes, keeps the id ---------
def test_p6t5_partner_call_agy_resume(tmp_path, monkeypatch):
    _agy_env(monkeypatch)
    uid = "a3907c80-1111-4222-8333-444455556666"
    out = gc.partner_call("review", "x", session=uid,
                          config_path=_cfg_file(tmp_path, engine="agy-print"))
    assert out.status == "ok"
    assert out.session == uid  # resume kept the conversation id


# --- P6-T6: the per-call tmp log is unlinked in finally ----------------------
def test_p6t6_log_unlinked(monkeypatch):
    seen = {}
    orig = gt._extract_conversation_id

    def spy(p):
        seen["path"] = p
        return orig(p)
    monkeypatch.setattr(gt, "_extract_conversation_id", spy)
    _agy_env(monkeypatch)
    _run(session=None)
    import os
    assert seen.get("path")
    assert not os.path.exists(seen["path"])  # cleaned up


# --- P6-T3c (review I-1): a resume that agy silently FORKS raises loud --------
def test_p6t3c_silent_fork_on_resume_raises(monkeypatch):
    # agy exits 0 but logs a DIFFERENT conversation id (started fresh on an
    # unknown/expired id) — must raise, never return fresh content as a continuation.
    _agy_env(monkeypatch, "--fork-on-resume")
    with pytest.raises(gt.AcpError):
        _run(session="a3907c80-1111-4222-8333-444455556666")


# --- P6-T3d (review I-1): a resume with no id in the log warns, does not raise -
def test_p6t3d_resume_unconfirmed_warns_not_raises(monkeypatch, capsys):
    _agy_env(monkeypatch, "--no-uuid-log")
    rr = _run(session="a3907c80-1111-4222-8333-444455556666")  # must NOT raise
    assert rr.session == "a3907c80-1111-4222-8333-444455556666"
    assert "could not confirm" in capsys.readouterr().err.lower()
