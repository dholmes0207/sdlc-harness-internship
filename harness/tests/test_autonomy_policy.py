"""test_autonomy_policy.py — deterministic resolver for the cook pause cadence.

autonomy_policy turns the HARNESS_AUTONOMY env knob (default|ask_all|god) into a
concrete per-boundary pause decision, so hs:cook consults ONE code source of truth
instead of re-interpreting prose. Wiring the knob that 13 doc surfaces already
describe; the levels match cook/SKILL.md "Pause cadence" + harness-contract.

Contract under test:
  - resolve_level(): env value when valid; missing OR invalid => "default"
    (fail-safe: an unknown level must not silently disable a pause).
  - should_pause(boundary, level): the level x boundary matrix. default pauses at
    plan_approval + ship; ask_all also after every phase; god pauses at none
    (hard stage gates are independent of this voluntary cadence).
  - an unknown boundary pauses (safe default — never skip a stop we don't know).
  - CLI --boundary <b> prints pause|continue + exit 0; --show emits JSON.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import autonomy_policy  # noqa: E402

_CLI = _SCRIPTS / "autonomy_policy.py"


def _run(args, env_level=None):
    env = dict(os.environ)
    env.pop("HARNESS_AUTONOMY", None)
    if env_level is not None:
        env["HARNESS_AUTONOMY"] = env_level
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        capture_output=True, text=True, env=env,
    )


# --- resolve_level --------------------------------------------------------

def test_resolve_level_reads_valid_env(monkeypatch):
    monkeypatch.setenv("HARNESS_AUTONOMY", "ask_all")
    assert autonomy_policy.resolve_level() == "ask_all"


def test_resolve_level_missing_env_is_default(monkeypatch):
    monkeypatch.delenv("HARNESS_AUTONOMY", raising=False)
    assert autonomy_policy.resolve_level() == "default"


def test_resolve_level_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("HARNESS_AUTONOMY", "yolo")
    assert autonomy_policy.resolve_level() == "default"


# --- should_pause matrix --------------------------------------------------

def test_default_pauses_at_plan_and_ship_not_phase():
    assert autonomy_policy.should_pause("plan_approval", "default") is True
    assert autonomy_policy.should_pause("ship", "default") is True
    assert autonomy_policy.should_pause("phase", "default") is False


def test_ask_all_also_pauses_after_every_phase():
    assert autonomy_policy.should_pause("phase", "ask_all") is True
    assert autonomy_policy.should_pause("plan_approval", "ask_all") is True
    assert autonomy_policy.should_pause("ship", "ask_all") is True


def test_god_pauses_at_no_voluntary_boundary():
    assert autonomy_policy.should_pause("plan_approval", "god") is False
    assert autonomy_policy.should_pause("phase", "god") is False
    assert autonomy_policy.should_pause("ship", "god") is False


def test_unknown_boundary_pauses_safely():
    # A boundary we don't recognize must default to pausing, never skipping.
    assert autonomy_policy.should_pause("mystery", "god") is True


def test_should_pause_resolves_level_from_env_when_omitted(monkeypatch):
    monkeypatch.setenv("HARNESS_AUTONOMY", "god")
    assert autonomy_policy.should_pause("phase") is False


# --- CLI ------------------------------------------------------------------

def test_cli_boundary_phase_default_continues():
    r = _run(["--boundary", "phase"])  # no env => default
    assert r.returncode == 0
    assert r.stdout.strip() == "continue"


def test_cli_boundary_phase_ask_all_pauses():
    r = _run(["--boundary", "phase"], env_level="ask_all")
    assert r.returncode == 0
    assert r.stdout.strip() == "pause"


def test_cli_show_emits_level_and_matrix():
    r = _run(["--show"], env_level="god")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["level"] == "god"
    assert payload["pauses"]["ship"] is False
