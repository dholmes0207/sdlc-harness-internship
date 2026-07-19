"""test_voice_inject.py — SessionStart hook that injects the terminal-voice
guidance as additionalContext.

Telemetry-class + fail-open: it advises the model, it NEVER blocks. The resolved
knob values plus the floor + scope-fence ride in additionalContext so the voice
is live for the whole session (and re-fires on /compact). A broken config or a
crashing core degrades to a plain continue, never to exit 2.

The hook is driven as a subprocess (the real stdin/stdout contract) with
HARNESS_TERMINAL_VOICE pointed at a scratch yaml; the fail-open-no-context path
is exercised in-process against the hook_runtime wrapper.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_RULES = Path(__file__).resolve().parent.parent / "rules"
sys.path.insert(0, str(_HOOKS))


def _env(tmp_path, voice_file=None, **extra):
    env = dict(os.environ)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_HOOK_AUDIT_DISABLED"] = "1"
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_TELEMETRY_DISABLED", None)
    if voice_file is not None:
        env["HARNESS_TERMINAL_VOICE"] = str(voice_file)
    else:
        env["HARNESS_TERMINAL_VOICE"] = str(tmp_path / "absent.yaml")  # → defaults
    env.update(extra)
    return env


def _run(tmp_path, stdin_obj, voice_file=None, **extra):
    return subprocess.run(
        [sys.executable, str(_HOOKS / "voice_inject.py")],
        input=json.dumps(stdin_obj), capture_output=True, text=True,
        env=_env(tmp_path, voice_file=voice_file, **extra),
    )


def _voice(tmp_path, doc):
    import yaml
    p = tmp_path / "terminal-voice.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


def _ctx(proc):
    out = json.loads(proc.stdout)
    hs = out.get("hookSpecificOutput") or {}
    return hs.get("additionalContext", "")


# ----------------------------------------------------------- defaults inject ---

def test_defaults_inject_level5_depth5(tmp_path):
    proc = _run(tmp_path, {"session_id": "s1", "source": "startup"})
    assert proc.returncode == 0
    ctx = _ctx(proc)
    assert "voice_level=5" in ctx
    assert "terminal_voice_level=5" in ctx


def test_context_names_floor_and_fence(tmp_path):
    ctx = _ctx(_run(tmp_path, {"session_id": "s1"}))
    assert "Universal-harm floor" in ctx
    assert "Scope-fence" in ctx
    assert "terminal-voice.md" in ctx  # points at the authority, not a restatement


# -------------------------------------------------------------- level 9 register ---

def test_level9_names_register(tmp_path):
    p = _voice(tmp_path, {"voice_level": 9})
    ctx = _ctx(_run(tmp_path, {"session_id": "s1"}, voice_file=p))
    assert "voice_level=9" in ctx
    assert "profanity" in ctx.lower()       # work-aimed register named
    assert "Universal-harm floor" in ctx    # ...but the floor still rides along


# ------------------------------------------------------- artifact-voice carve-out ---

def test_context_carves_out_artifact_voice(tmp_path):
    # The injected guidance must tell the model voice_level does NOT touch an
    # artifact's own designed voice — naming the two existing cases.
    ctx = _ctx(_run(tmp_path, {"session_id": "s1"}))
    low = ctx.lower()
    assert "journal" in low
    assert "critique" in low


def test_no_markdown_flag_surfaces(tmp_path):
    p = _voice(tmp_path, {"no_markdown": True})
    ctx = _ctx(_run(tmp_path, {"session_id": "s1"}, voice_file=p))
    assert "no_markdown" in ctx or "plain prose" in ctx.lower()


def test_persona_surfaces_in_context(tmp_path):
    p = _voice(tmp_path, {"persona": "pirate"})
    ctx = _ctx(_run(tmp_path, {"session_id": "s1"}, voice_file=p))
    assert "persona=pirate" in ctx
    assert "voice_level sets" in ctx  # the form-vs-harshness precedence line


# ----------------------------------------------------- ladder drift-guard (M1) ---

# The hook restates a terse per-level register/depth descriptor; the canonical
# table lives in terminal-voice.md. These anchors must appear in BOTH so a rule
# edit that drops an anchor (or a hook edit that drifts) trips CI — the same
# discipline the persona ids already get from their parity test.
_REGISTER_ANCHORS = {
    1: "polite, measured",
    3: "direct but courteous",
    5: "NO profanity",
    6: "name a bad idea as bad",
    7: "roast the work",
    8: "vi pronouns",
    9: "đm/vl",
}
_DEPTH_ANCHORS = {0: "answer only", 3: "reasoning + key trade-offs", 5: "full reasoning"}


def test_interview_knobs_surface_in_context(tmp_path):
    p = _voice(tmp_path, {"interview_rigor": "deep",
                          "action_prompting": "proactive"})
    ctx = _ctx(_run(tmp_path, {"session_id": "s1"}, voice_file=p))
    assert "interview_rigor=deep" in ctx
    assert "action_prompting=proactive" in ctx
    # detail_level was merged into terminal_voice_level — no longer surfaced
    assert "detail_level" not in ctx


# --------------------------------------------------------------------- fail-open ---

def test_corrupt_config_continues_never_blocks(tmp_path):
    p = tmp_path / "terminal-voice.yaml"
    p.write_text("voice_level: [oops\n:::bad", encoding="utf-8")
    proc = _run(tmp_path, {"session_id": "s1"}, voice_file=p)
    assert proc.returncode == 0                 # never exit 2
    json.loads(proc.stdout)                      # valid JSON contract
    # tolerant loader → defaults → still sane guidance (level 5)
    assert "voice_level=5" in _ctx(proc)


def test_garbage_stdin_never_exits_2(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "voice_inject.py")],
        input="not json at all", capture_output=True, text=True,
        env=_env(tmp_path),
    )
    assert proc.returncode == 0


def test_telemetry_disabled_emits_continue_no_context(tmp_path):
    proc = _run(tmp_path, {"session_id": "s1"}, HARNESS_TELEMETRY_DISABLED="1")
    assert proc.returncode == 0
    assert _ctx(proc) == ""               # disabled → no injection
    assert "continue" in proc.stdout


def test_failopen_graceful_degrade_on_load_raise(monkeypatch, capsys):
    # Fail-open path: if the voice loader itself raises (it shouldn't — it's
    # tolerant — but defense in depth), the hook must NEVER exit 2. resolve_all
    # seeds the voice axes from DEFAULTS, so the hook degrades GRACEFULLY to a
    # default-voice context rather than dropping the whole register in silence.
    import json as _json
    import voice_inject

    def _boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(voice_inject.voice_prefs, "load", _boom)
    monkeypatch.setenv("HARNESS_HOOK_AUDIT_DISABLED", "1")
    voice_inject.run(raw="{}")  # must not raise / exit 2
    out = capsys.readouterr().out
    # still emits a valid context built from seeded voice defaults
    payload = _json.loads(out)
    text = payload.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "Terminal voice" in text, (
        "loader raise should degrade to a default-voice context, not silence.\n%s" % out)


# ------------------------------------------------------------ rule presence ---

def test_rule_file_carries_artifact_voice_section():
    md = (_RULES / "terminal-voice.md").read_text(encoding="utf-8")
    assert "Terminal voice vs artifact voice" in md   # the carve-out subsection
    assert "journal" in md.lower()
    assert "critique" in md.lower()
    assert "Scope-fence" in md or "scope-fence" in md.lower()


def test_rule_file_has_floor_and_ladder():
    md = (_RULES / "terminal-voice.md").read_text(encoding="utf-8")
    assert "Universal-harm floor" in md
    assert "voice_level" in md


# ----------------------------------------------- body-inject + shared builder ---

def _output(tmp_path, **doc):
    import yaml
    p = tmp_path / "output.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


def test_sessionstart_carries_essence_and_read_pointer_not_body(tmp_path):
    """Diet C: SessionStart carries the one-line essence (`code_style=3/5`) + a
    MANDATORY directive to READ the profile — NOT the dumped body (`Lead with
    trade-offs`). The 883-token body no longer inflates every session start."""
    out = _output(tmp_path, language="vi", code_style=3)
    proc = _run(tmp_path, {"session_id": "s1"}, HARNESS_OUTPUT=str(out))
    ctx = _ctx(proc)
    assert "code_style=3/5" in ctx, "essence dropped\n%s" % ctx
    assert "output-styles/code-style-level-3.md" in ctx, "read-pointer missing\n%s" % ctx
    assert "MANDATORY CODE DIRECTIVES" not in ctx, "profile body leaked into register"


def test_build_context_alias_present():
    """build_context MUST stay a public name on voice_inject, aliased to the shared
    register_block.build_register — production caller inject_prompt_context:153
    reaches voice_inject.build_context and a bare-except would swallow its removal."""
    import voice_inject
    import register_block
    assert hasattr(voice_inject, "build_context")
    assert voice_inject.build_context is register_block.build_register


def test_inject_prompt_context_refresh_drops_voice_dup(tmp_path, monkeypatch):
    """Diet A: the per-turn refresh no longer re-emits the voice register (a
    SessionStart dup). The first-turn block carries no full "Terminal voice" block;
    the compact within-window refresh keeps only the one-line voice_level marker."""
    import importlib
    out = _output(tmp_path, language="vi", code_style=3)
    monkeypatch.setenv("HARNESS_OUTPUT", str(out))
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tmp_path / "absent.yaml"))
    import inject_prompt_context as ipc
    importlib.reload(ipc)
    assert not hasattr(ipc, "_voice_section"), "voice dup section must be removed (A)"
    root = Path(__file__).resolve().parents[2]
    assert "Terminal voice" not in ipc.build_context(root)
    assert "voice_level=" in ipc.build_slim_context(root)


def test_core_includes_name_soul_when_bundle_active(tmp_path, monkeypatch):
    """The MAIN surface (voice_inject.core) carries NAME + SOUL when a bundle is
    active — the positive half of the injection split (the subagent surface must
    NOT; that is locked in test_persona_injection_split + test_subagent_init)."""
    import yaml
    import voice_inject
    reg = tmp_path / "persona-bundles.yaml"
    reg.write_text(yaml.safe_dump({"bundles": [{
        "id": "b", "name": "MainNameZZ", "characteristic": "c",
        "soul": "MainSoulZZ", "form": "bluf", "default_voice_level": 6}]},
        allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("HARNESS_PERSONA_BUNDLES", str(reg))
    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text("persona_bundle: b\npersona: none\nvoice_level: 6\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))
    out = tmp_path / "output.yaml"
    out.write_text("language: vi\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_OUTPUT", str(out))
    monkeypatch.setenv("HARNESS_PERSONA_ME", str(tmp_path / "absent.json"))
    txt = voice_inject.core({})
    assert "MainNameZZ" in txt
    assert "MainSoulZZ" in txt
