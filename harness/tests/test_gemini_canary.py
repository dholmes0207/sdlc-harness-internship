"""Setup canary + worktree reap (phase 10).

`setup` runs a print canary probe — one `gemini -p "ok" -o json` — and WARNS loudly
on drift (unreachable, or an empty response) without ever blocking setup (D12). It
also reaps orphan sandbox worktrees left by a crashed write. Driven as a subprocess
with the fake gemini print CLI (drift shaped by flags); the live probe is opt-in.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_COMPANION = _ROOT / "plugins" / "hs" / "scripts" / "gemini_companion.py"
_FAKE = _ROOT / "tests" / "fixtures" / "fake_gemini_print.py"
_FAKE_AGY = _ROOT / "tests" / "fixtures" / "fake_agy.py"

_LANE = {
    "master": "on", "mode": "partner", "write": "read_only", "stop_review_gate": "off",
    "purposes": {"research": "flash", "scout": "flash", "review": "pro",
                 "critique": "pro", "redteam": "pro", "delegate": "pro", "fix": "pro"},
    "route_all_surface": ["research", "scout"], "overrides": {},
    "timeouts": {"default": 10}, "retry": {"max_attempts": 1, "on_markers": []},
    "secret_scrub": "warn",
}


def _run_setup(tmp_path, *fake_flags, lane_over=None, state_dir=None, agy_flags=()):
    lane = tmp_path / "lane.yaml"
    lane.write_text(yaml.safe_dump({**_LANE, **(lane_over or {})}, sort_keys=False),
                    encoding="utf-8")
    # Always wire a FAKE agy too: an `auto` lane probes agy in the canary, and
    # without the seam that would spawn the REAL agy (Google OAuth browser) here.
    env = {**os.environ,
           "HARNESS_STATE_DIR": state_dir or str(tmp_path / "state"),
           "HARNESS_GEMINI_PARTNER": str(lane),
           "HARNESS_GEMINI_PRINT_CMD": "%s %s %s" % (sys.executable, _FAKE, " ".join(fake_flags)),
           "HARNESS_AGY_CMD": "%s %s %s" % (sys.executable, _FAKE_AGY,
                                            " ".join(str(x) for x in agy_flags))}
    return subprocess.run([sys.executable, str(_COMPANION), "setup", "--config", str(lane)],
                          capture_output=True, text=True, env=env)


# --- T1: gemini reachable + answering → clean, no warnings ------------------
def test_t1_canary_reachable_clean(tmp_path):
    r = _run_setup(tmp_path)  # fake echoes "ok" → non-empty response
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["canary"]["reachable"] is True
    assert out["canary"]["warnings"] == []


# --- T2: gemini unreachable (down) → warn, never block ----------------------
def test_t2_canary_down_warns_not_blocks(tmp_path):
    r = _run_setup(tmp_path, "--exit-code", "1", "--stderr-marker", "auth_failed")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["canary"]["reachable"] is False
    assert "canary" in r.stderr.lower()


# --- T3: gemini answers empty → shape-drift warn (still reachable) ----------
def test_t3_canary_empty_response_drift_warns(tmp_path):
    r = _run_setup(tmp_path, "--empty-response")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["canary"]["reachable"] is True
    assert out["canary"]["warnings"]  # empty-response drift surfaced
    assert "drift" in r.stderr.lower()


# --- T5: orphan worktrees are reaped ----------------------------------------
def test_t5_reaps_orphan_worktrees(tmp_path):
    state = tmp_path / "state"
    orphan = state / "gemini" / "worktrees" / "deadjob"
    orphan.mkdir(parents=True)
    (orphan / "leftover.txt").write_text("stale", encoding="utf-8")
    r = _run_setup(tmp_path, state_dir=str(state))
    assert r.returncode == 0
    assert not orphan.exists(), "orphan worktree should be reaped"
    assert json.loads(r.stdout)["reaped_worktrees"] >= 1


# --- Phase 7: agy canary (per-transport) + inert-auth-early -----------------
def test_p7t1_agy_pin_canary_reachable(tmp_path):
    r = _run_setup(tmp_path, lane_over={"engine": "agy-print"})
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["canary_agy"]["reachable"] is True
    assert out["canary"] is None  # gemini not probed when agy-print is pinned


def test_p7t2_agy_not_logged_in_warns_not_blocks(tmp_path):
    r = _run_setup(tmp_path, lane_over={"engine": "agy-print"},
                   agy_flags=("--exit-code", 1, "--stderr-marker", "not_logged_in"))
    assert r.returncode == 0  # D18: warn, never block
    assert json.loads(r.stdout)["canary_agy"]["reachable"] is False
    assert "log in" in r.stderr.lower() and "agy" in r.stderr.lower()


def test_p7t3_agy_logformat_drift_warns(tmp_path):
    r = _run_setup(tmp_path, lane_over={"engine": "agy-print"},
                   agy_flags=("--no-uuid-log",))
    assert r.returncode == 0
    out = json.loads(r.stdout)["canary_agy"]
    assert out["reachable"] is True
    assert out["conversation_id_recovered"] is False
    assert "drift" in r.stderr.lower()


def test_p7t4_auto_probes_both_engines(tmp_path):
    r = _run_setup(tmp_path, lane_over={"engine": "auto"})  # dummy key → not inert
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["canary"] is not None and out["canary"]["reachable"] is True  # gemini
    assert out["canary_agy"]["reachable"] is True                            # agy


def test_p7t5_auto_no_creds_inert_auth_early(tmp_path):
    lane = tmp_path / "lane.yaml"
    lane.write_text(yaml.safe_dump({**_LANE, "engine": "auto"}, sort_keys=False),
                    encoding="utf-8")
    # no key, no agy override, agy home absent → the lane can reach no engine
    env = {k: v for k, v in os.environ.items()
           if k not in ("GEMINI_API_KEY", "HARNESS_AGY_CMD")}
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_GEMINI_PARTNER"] = str(lane)
    env["HARNESS_AGY_HOME"] = str(tmp_path / "no-agy-home")
    r = subprocess.run([sys.executable, str(_COMPANION), "setup", "--config", str(lane)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout)["canary"]["inert_auth"] is True
    # P7-T5b scenario-HIGH: still names how to enable the lane, never a silent abort
    assert "GEMINI_API_KEY" in r.stderr and "agy" in r.stderr.lower()


# --- T6: live gemini (opt-in) -----------------------------------------------
@pytest.mark.real_gemini
def test_t6_real_gemini_canary(tmp_path):
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("no GEMINI_API_KEY — live lane opt-in")
    lane = tmp_path / "lane.yaml"
    lane.write_text(yaml.safe_dump(_LANE, sort_keys=False), encoding="utf-8")
    env = {**os.environ, "HARNESS_STATE_DIR": str(tmp_path / "state"),
           "HARNESS_GEMINI_PARTNER": str(lane)}
    r = subprocess.run([sys.executable, str(_COMPANION), "setup", "--config", str(lane)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
