"""test_compact_register_survival.py — lock that voice + output register survive
compaction.

When CC compacts a long conversation it fires SessionStart with source="compact".
voice_inject.run() does NOT branch on source (voice_inject.py:165-178 -> core ->
build_context), so a compact event must inject the SAME register as startup: the
terminal-voice axes (voice_level / persona) AND the output-config register
(audience / code_style / humanize). This was already true by construction; until
now NO test passed source="compact", so a future refactor could silently start
branching on source and drop the register after every compaction without tripping
the suite. This is that tripwire.

Test-lock, not a build: it asserts existing-correct behavior. If #1-#4 ever go
RED, the lock has caught a real regression (or an intentional change that must be
re-litigated) — it is NOT to be "fixed" by loosening the assertions.

Driven as a subprocess (the real stdin/stdout contract), mirroring
test_voice_inject, with HARNESS_TERMINAL_VOICE + HARNESS_OUTPUT pointed at scratch
files set to NON-DEFAULT values — so a pass proves resolve_all actually honored
the env, guarding against the "override reaches read but not hook" split-brain.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


_HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def _scratch_voice(tmp_path):
    import yaml
    p = tmp_path / "terminal-voice.yaml"
    p.write_text(yaml.safe_dump({"voice_level": 9, "persona": "reality-check"}),
                 encoding="utf-8")
    return p


def _scratch_output(tmp_path):
    import yaml
    p = tmp_path / "output.yaml"
    # non-default: audience and code_style default absent (None), humanize default
    # False — set all three so their markers MUST appear if the env is honored.
    p.write_text(yaml.safe_dump(
        {"language": "vi", "audience": 0, "code_style": 3, "humanize": True}),
        encoding="utf-8")
    return p


def _env(tmp_path):
    env = dict(os.environ)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_HOOK_AUDIT_DISABLED"] = "1"
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_TELEMETRY_DISABLED", None)
    env["HARNESS_TERMINAL_VOICE"] = str(_scratch_voice(tmp_path))
    env["HARNESS_OUTPUT"] = str(_scratch_output(tmp_path))
    return env


def _ctx(tmp_path, source):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "voice_inject.py")],
        input=json.dumps({"hook_event_name": "SessionStart",
                          "session_id": "s1", "source": source}),
        capture_output=True, text=True, env=_env(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


# ------------------------------------------------------- 1. compact injects context ---

def test_compact_injects_nonempty_context(tmp_path):
    assert _ctx(tmp_path, "compact").strip() != ""


# --------------------------------------------------------- 2. voice register present ---

def test_compact_carries_voice_register(tmp_path):
    ctx = _ctx(tmp_path, "compact")
    assert "voice_level=9" in ctx            # env honored, not a baked default
    assert "persona=reality-check" in ctx


# -------------------------------------------------------- 3. output register present ---

def test_compact_carries_output_register(tmp_path):
    ctx = _ctx(tmp_path, "compact")
    assert "audience=0" in ctx                # reader register survived compact
    assert "code_style=3" in ctx             # generated-code register survived
    assert "humanize=on" in ctx              # humanizer flag survived


# ---------------------------------------------------- 4. compact == startup parity ---

def test_compact_matches_startup_register(tmp_path):
    compact = _ctx(tmp_path, "compact")
    startup = _ctx(tmp_path, "startup")
    for marker in ("voice_level=9", "persona=reality-check",
                   "audience=0", "code_style=3", "humanize=on"):
        assert (marker in compact) == (marker in startup) is True, (
            "register marker %r differs between compact and startup -> compact "
            "is degrading the register" % marker)


# ------------------------------------------ 6. persona-bundle NAME/SOUL survive compact ---

def _bundle_env(tmp_path):
    import yaml
    env = _env(tmp_path)  # reuse the state/log/audit seams
    reg = tmp_path / "persona-bundles.yaml"
    reg.write_text(yaml.safe_dump({"bundles": [{
        "id": "b", "name": "CompactNameZZ", "characteristic": "c",
        "soul": "CompactSoulZZ", "form": "bluf", "default_voice_level": 6}]},
        allow_unicode=True), encoding="utf-8")
    tv = tmp_path / "bundle-voice.yaml"
    tv.write_text("persona_bundle: b\npersona: none\nvoice_level: 6\n", encoding="utf-8")
    env["HARNESS_PERSONA_BUNDLES"] = str(reg)
    env["HARNESS_TERMINAL_VOICE"] = str(tv)
    env["HARNESS_PERSONA_ME"] = str(tmp_path / "absent.json")
    return env


def _bundle_ctx(tmp_path, source):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "voice_inject.py")],
        input=json.dumps({"hook_event_name": "SessionStart",
                          "session_id": "s1", "source": source}),
        capture_output=True, text=True, env=_bundle_env(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


def test_bundle_name_soul_survive_compact(tmp_path):
    compact = _bundle_ctx(tmp_path, "compact")
    startup = _bundle_ctx(tmp_path, "startup")
    assert "CompactNameZZ" in compact and "CompactSoulZZ" in compact
    for marker in ("CompactNameZZ", "CompactSoulZZ"):
        assert (marker in compact) == (marker in startup) is True, (
            "bundle marker %r differs between compact and startup" % marker)


# -- 5. cross-ref only: inject_prompt_context re-arm on compact is locked elsewhere --
# inject_prompt_context's compact behavior (throttle re-arm) is already covered by
# test_inject_prompt_context.py::test_compact_rearms_after_throttle — not duplicated
# here. This file owns ONLY the voice_inject register-survival lock.
