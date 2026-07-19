"""Stop-review-gate contract (phase 6) — the RT-02 mitigation.

The gate is driven as a real subprocess with a real stdin payload (code-standards
§7: never import-cheat a fail-open contract). Structural safety: in the shipped
`advisory` mode the hook prints to stderr and NEVER emits a block, so it
cannot re-invoke the model and cannot brick a session (RT-02). A down gemini, an
active /goal, a tripped breaker, or a crashing config all fail OPEN (exit 0). Only
opt-in `enforce` emits a Stop `decision: block` — once, goal-gated, breaker-guarded.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _ROOT / "hooks" / "gemini_stop_review_gate.py"
_FAKE = _ROOT / "tests" / "fixtures" / "fake_gemini_print.py"
_FAKE_AGY = _ROOT / "tests" / "fixtures" / "fake_agy.py"

_LANE = {
    "master": "on", "mode": "partner", "write": "read_only", "stop_review_gate": "advisory",
    "purposes": {"research": "flash", "scout": "flash", "review": "pro",
                 "critique": "pro", "redteam": "pro", "delegate": "pro", "fix": "pro"},
    "route_all_surface": ["research", "scout"], "overrides": {},
    "timeouts": {"default": 10}, "retry": {"max_attempts": 1, "on_markers": []},
    "secret_scrub": "warn",
}


def _write_yaml(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return str(path)


def _transcript(tmp, goal_met=None, assistant="did the thing"):
    p = tmp / "transcript.jsonl"
    lines = [{"type": "assistant", "message": {"content": [{"type": "text", "text": assistant}]}}]
    if goal_met is not None:
        lines.append({"attachment": {"type": "goal_status", "met": goal_met}})
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return str(p)


def _run(tmp, *, lane_over=None, hook_enabled=True, print_cmd=None, agy_cmd=None,
         goal_met=None, state_dir=None):
    lane = _write_yaml(tmp / "lane.yaml", {**_LANE, **(lane_over or {})})
    hooks_cfg = _write_yaml(
        tmp / "hooks.yaml",
        {"hooks": {"gemini_stop_review_gate": {"enabled": hook_enabled}}})
    # Always wire a FAKE agy: the lane defaults to engine=auto, so a down gemini
    # (T2) falls back to agy — and without this seam that fallback would spawn the
    # REAL agy (Google-OAuth browser) on the wire. Mirrors the canary suite's
    # _run_setup, which pins the same seam for the same reason.
    env = {**os.environ,
           "HARNESS_STATE_DIR": state_dir or str(tmp / "state"),
           "HARNESS_GEMINI_PARTNER": lane,
           "HARNESS_HOOK_CONFIG": hooks_cfg,
           "HARNESS_AGY_CMD": agy_cmd or _fake_agy()}
    if print_cmd:
        env["HARNESS_GEMINI_PRINT_CMD"] = print_cmd
    payload = {"stop_hook_active": True, "session_id": "s1",
               "transcript_path": _transcript(tmp, goal_met=goal_met)}
    return subprocess.run([sys.executable, str(_HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def _fake(verdict=None, down=False):
    """Build a HARNESS_GEMINI_PRINT_CMD pointing at fake_gemini_print: `down` makes
    it emit non-JSON so the transport raises (fail-open path); `verdict` emits a
    `VERDICT:<v>` response the stop-gate parses; default echoes the prompt (PASS)."""
    cmd = "%s %s" % (sys.executable, _FAKE)
    if down:
        return cmd + " --bad-json"
    if verdict:
        return cmd + " --emit-response VERDICT:%s" % verdict
    return cmd


def _fake_agy(down=False):
    """Build a HARNESS_AGY_CMD pointing at fake_agy: `down` makes it exit non-zero
    (logged-out marker) so the agy fallback fails too; default answers ok."""
    cmd = "%s %s" % (sys.executable, _FAKE_AGY)
    if down:
        return cmd + " --exit-code 1 --stderr-marker not_logged_in"
    return cmd


# --- T1: stop_review_gate off → silent allow --------------------------------
def test_t1_gate_off_allows(tmp_path):
    r = _run(tmp_path, lane_over={"stop_review_gate": "off"})
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"continue": True}
    assert '"decision"' not in r.stdout


# --- T2: advisory + both engines down → fail-open, no block -----------
def test_t2_advisory_down_failopen(tmp_path):
    # gemini errors mid-prompt AND the auto agy fallback is down too — the genuine
    # both-engines-down fail-open path, kept entirely off the wire.
    r = _run(tmp_path, print_cmd=_fake(down=True), agy_cmd=_fake_agy(down=True))
    assert r.returncode == 0
    assert '"decision"' not in r.stdout


# --- T3: advisory + gemini ok → review to STDERR only -----------------------
def test_t3_advisory_ok_stderr_only(tmp_path):
    r = _run(tmp_path, print_cmd=_fake())
    assert r.returncode == 0
    assert '"decision"' not in r.stdout
    assert "gemini" in r.stderr.lower()  # review surfaced on stderr


# --- T4: enforce + /goal active → emit NOTHING (S3) -------------------------
def test_t4_enforce_goal_active_emits_nothing(tmp_path):
    r = _run(tmp_path, lane_over={"stop_review_gate": "enforce"},
             print_cmd=_fake(verdict="FAIL"), goal_met=False)
    assert r.returncode == 0
    # never FIGHT the /goal loop: no decision:block, no additionalContext model channel.
    # A bare {"continue": true} (or empty) is non-blocking and does not re-invoke.
    out = r.stdout.strip()
    assert '"decision"' not in out and "hookSpecificOutput" not in out
    if out:
        assert json.loads(out).get("continue") is True


# --- T5: enforce + goal inactive + FAIL + breaker ok → block once
def test_t5_enforce_fail_emits_additional_context(tmp_path):
    r = _run(tmp_path, lane_over={"stop_review_gate": "enforce"},
             print_cmd=_fake(verdict="FAIL"))
    assert r.returncode == 0
    assert '"decision"' in r.stdout


# --- T5b: enforce + PASS verdict with "FAIL" in the body → no block ----------
def test_t5b_enforce_pass_verdict_ignores_body_fail(tmp_path):
    # Review finding: _verdict must read the VERDICT prefix, not grep the body —
    # the review prompt itself contains the word FAIL, so a PASS review must not
    # false-trigger enforcement.
    r = _run(tmp_path, lane_over={"stop_review_gate": "enforce"},
             print_cmd=_fake(verdict="PASS"))
    assert r.returncode == 0
    assert '"decision"' not in r.stdout


# --- T6: enforce + breaker already tripped → allow --------------------------
def test_t6_enforce_breaker_tripped_allows(tmp_path):
    state = tmp_path / "state"
    (state / "gemini").mkdir(parents=True)
    (state / "gemini" / "stop_breaker.json").write_text(
        json.dumps({"s1": 99}), encoding="utf-8")
    r = _run(tmp_path, lane_over={"stop_review_gate": "enforce"},
             print_cmd=_fake(verdict="FAIL"), state_dir=str(state))
    assert r.returncode == 0
    assert '"decision"' not in r.stdout


# --- T7: master off normalizes the gate off (S6) ----------------------------
def test_t7_master_off_allows(tmp_path):
    r = _run(tmp_path, lane_over={"master": "off", "stop_review_gate": "enforce"},
             print_cmd=_fake(verdict="FAIL"))
    assert r.returncode == 0
    assert '"decision"' not in r.stdout


# --- T8: a crashing/malformed config still fails OPEN (F6) -------------------
def test_t8_broken_config_failopen(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("master: on\nmode: [unterminated\n", encoding="utf-8")
    hooks_cfg = _write_yaml(tmp_path / "hooks.yaml",
                            {"hooks": {"gemini_stop_review_gate": {"enabled": True}}})
    env = {**os.environ, "HARNESS_STATE_DIR": str(tmp_path / "state"),
           "HARNESS_GEMINI_PARTNER": str(bad), "HARNESS_HOOK_CONFIG": hooks_cfg}
    payload = {"stop_hook_active": True, "session_id": "s1",
               "transcript_path": _transcript(tmp_path)}
    r = subprocess.run([sys.executable, str(_HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0  # config crash must never exit 2 (fail-open)
    assert '"decision"' not in r.stdout
