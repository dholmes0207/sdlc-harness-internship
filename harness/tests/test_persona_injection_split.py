"""Injection split — the security invariant of the persona bundle.

Three data pieces travel TWO separate injection paths:

  FORM          → build_register (SHARED: SessionStart + SubagentStart)   — safe
  NAME + SOUL   → appended in voice_inject.core() only                    — MAIN only
  RELATIONSHIP  → appended in voice_inject.core() only, DOUBLE-GATED      — MAIN only

If NAME/SOUL/RELATIONSHIP ever reached build_register, subagent_init (which calls
build_register directly) would inject them into a subagent surface → a report or
PII into git history, unrecoverable. The presence-gate is the core security check:
the subagent surface must contain ZERO fragments of NAME/SOUL/RELATIONSHIP, while
the main surface (positive control) contains all three. FORM is allowed on both.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
for _p in (_SCRIPTS, _HOOKS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

NAME_S = "ZoltarNameSentinel"
CHAR_S = "CharSentinelPlugh"
SOUL_S = "SoulSentinelXyzzy"
REL_S = "RelSentinelQwerty"
FORM = "military"


def _seam_output(tmp_path, monkeypatch):
    outp = tmp_path / "output.yaml"
    outp.write_text("language: vi\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_OUTPUT", str(outp))


def _setup_active_bundle(tmp_path, monkeypatch, with_pii=True):
    import yaml
    reg = tmp_path / "persona-bundles.yaml"
    reg.write_text(yaml.safe_dump({"bundles": [{
        "id": "sentinel-bundle", "name": NAME_S, "characteristic": CHAR_S,
        "soul": SOUL_S, "form": FORM, "default_voice_level": 7,
    }]}, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("HARNESS_PERSONA_BUNDLES", str(reg))

    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text("persona_bundle: sentinel-bundle\nvoice_level: 7\npersona: none\n",
                  encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))

    _seam_output(tmp_path, monkeypatch)

    if with_pii:
        me = tmp_path / "persona-me.json"
        me.write_text(json.dumps({"name": REL_S, "role": "owner"}), encoding="utf-8")
        monkeypatch.setenv("HARNESS_PERSONA_ME", str(me))
    else:
        monkeypatch.setenv("HARNESS_PERSONA_ME", str(tmp_path / "absent.json"))


# --- the security presence-gate ---

def test_main_surface_includes_name_soul_relationship(tmp_path, monkeypatch):
    _setup_active_bundle(tmp_path, monkeypatch, with_pii=True)
    import voice_inject
    txt = voice_inject.core({})
    assert NAME_S in txt   # positive control: the sentinels ARE injected somewhere
    assert CHAR_S in txt
    assert SOUL_S in txt
    assert REL_S in txt


def test_subagent_surface_excludes_name_soul_relationship(tmp_path, monkeypatch):
    _setup_active_bundle(tmp_path, monkeypatch, with_pii=True)
    import subagent_init
    txt = subagent_init.context_text({"agent_type": "tester"})
    assert NAME_S not in txt
    assert CHAR_S not in txt
    assert SOUL_S not in txt
    assert REL_S not in txt


def test_subagent_surface_includes_form(tmp_path, monkeypatch):
    _setup_active_bundle(tmp_path, monkeypatch, with_pii=True)
    import subagent_init
    txt = subagent_init.context_text({"agent_type": "tester"})
    assert ("persona=%s" % FORM) in txt  # the bundle FORM is safe on a subagent surface


# --- RELATIONSHIP double-gate ---

def test_relationship_gated_off_when_no_pii_file(tmp_path, monkeypatch):
    _setup_active_bundle(tmp_path, monkeypatch, with_pii=False)
    import voice_inject
    txt = voice_inject.core({})
    assert NAME_S in txt      # NAME/SOUL still present
    assert SOUL_S in txt
    assert REL_S not in txt   # RELATIONSHIP gated OFF: no PII file


def test_relationship_gated_off_when_bundle_null(tmp_path, monkeypatch):
    # PII file exists but no bundle active → NOTHING injected (name/soul/rel absent)
    me = tmp_path / "persona-me.json"
    me.write_text(json.dumps({"name": REL_S}), encoding="utf-8")
    monkeypatch.setenv("HARNESS_PERSONA_ME", str(me))
    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text("persona_bundle: null\nvoice_level: 5\npersona: bluf\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))
    _seam_output(tmp_path, monkeypatch)
    import voice_inject
    txt = voice_inject.core({})
    assert REL_S not in txt


# --- FORM resolution in build_register ---

def test_form_from_bundle_when_active(tmp_path, monkeypatch):
    _setup_active_bundle(tmp_path, monkeypatch, with_pii=False)
    import register_block
    import output_config
    reg = register_block.build_register(output_config.resolve_all())
    assert ("persona=%s" % FORM) in reg  # persona line uses the bundle form


def test_form_fallback_when_resolve_fails(tmp_path, monkeypatch):
    import yaml
    reg_file = tmp_path / "persona-bundles.yaml"
    reg_file.write_text(yaml.safe_dump({"bundles": []}, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("HARNESS_PERSONA_BUNDLES", str(reg_file))
    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text("persona_bundle: ghost-id\nvoice_level: 5\npersona: bluf\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))
    _seam_output(tmp_path, monkeypatch)
    import register_block
    import output_config
    reg = register_block.build_register(output_config.resolve_all())  # never raises
    assert "persona=bluf" in reg  # falls back to the persona knob


# --- F3 byte-identical null path (golden from HEAD, captured pre-edit) ---

def test_bundle_null_injection_byte_identical(tmp_path, monkeypatch):
    golden = json.loads((Path(__file__).resolve().parent / "fixtures"
                         / "persona_null_golden.json").read_text(encoding="utf-8"))
    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text(golden["config"]["terminal_voice_yaml"], encoding="utf-8")
    outp = tmp_path / "output.yaml"
    outp.write_text(golden["config"]["output_yaml"], encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))
    monkeypatch.setenv("HARNESS_OUTPUT", str(outp))
    # bundle + PII seams point at absent files → the null path is fully inert
    monkeypatch.setenv("HARNESS_PERSONA_BUNDLES", str(tmp_path / "absent-bundles.yaml"))
    monkeypatch.setenv("HARNESS_PERSONA_ME", str(tmp_path / "absent-me.json"))
    import register_block
    import output_config
    import voice_inject
    import subagent_init
    prefs = output_config.resolve_all()
    assert register_block.build_register(prefs) == golden["build_register"]
    assert voice_inject.core({}) == golden["voice_inject_core"]
    assert subagent_init.context_text({"agent_type": "tester"}) == golden["subagent_context_text"]


# --- per-turn slim pin: keep the character from drifting between /compact ---
# The SessionStart block scrolls out of attention in a long session; the UserPromptSubmit
# + Stop slim refresh re-asserts the CHARACTER (name + one-line gist) every turn. The full
# SOUL + RELATIONSHIP stay SessionStart-only. This path is MAIN-only (UPS/Stop never fire
# for a subagent), so the name/gist never reaches a subagent surface.

def _seam_null_bundle(tmp_path, monkeypatch):
    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text("persona_bundle: null\nvoice_level: 5\npersona: bluf\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))
    _seam_output(tmp_path, monkeypatch)


def test_slim_voice_pins_bundle_name_and_gist(tmp_path, monkeypatch):
    _setup_active_bundle(tmp_path, monkeypatch, with_pii=False)
    import inject_prompt_context as ipc
    slim = ipc._slim_voice()
    assert "persona_bundle=sentinel-bundle" in slim
    assert NAME_S in slim   # name pinned every turn
    assert CHAR_S in slim   # one-line gist pinned every turn
    assert SOUL_S not in slim  # full SOUL is NOT re-sent per turn (stays SessionStart-only)


def test_slim_voice_no_pin_when_bundle_null(tmp_path, monkeypatch):
    # User has NOT chosen a character → slim carries NO persona_bundle segment
    # (byte-identical to today's knob-only line).
    _seam_null_bundle(tmp_path, monkeypatch)
    import inject_prompt_context as ipc
    slim = ipc._slim_voice()
    assert "persona_bundle=" not in slim
    assert slim.startswith("voice_level=")


def test_slim_voice_no_pin_when_bundle_unresolvable(tmp_path, monkeypatch):
    import yaml
    reg = tmp_path / "persona-bundles.yaml"
    reg.write_text(yaml.safe_dump({"bundles": []}), encoding="utf-8")
    monkeypatch.setenv("HARNESS_PERSONA_BUNDLES", str(reg))
    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text("persona_bundle: ghost\nvoice_level: 5\npersona: bluf\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))
    _seam_output(tmp_path, monkeypatch)
    import inject_prompt_context as ipc
    assert "persona_bundle=" not in ipc._slim_voice()  # stale id → inert, never raises


def test_slim_voice_gist_is_capped(tmp_path, monkeypatch):
    import yaml
    long_char = "x" * 250
    reg = tmp_path / "persona-bundles.yaml"
    reg.write_text(yaml.safe_dump({"bundles": [{
        "id": "b", "name": "N", "characteristic": long_char, "soul": "s",
        "form": "bluf", "default_voice_level": 5}]}, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("HARNESS_PERSONA_BUNDLES", str(reg))
    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text("persona_bundle: b\nvoice_level: 5\npersona: bluf\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))
    _seam_output(tmp_path, monkeypatch)
    import inject_prompt_context as ipc
    slim = ipc._slim_voice()
    assert "persona_bundle=b" in slim
    assert long_char not in slim  # the 250-char trait is capped, not pinned whole


def test_context_sig_tracks_persona_bundle(tmp_path, monkeypatch):
    # A mid-session bundle change must change the re-inject fingerprint so the refresh fires.
    import yaml
    reg = tmp_path / "persona-bundles.yaml"
    reg.write_text(yaml.safe_dump({"bundles": [{
        "id": "b", "name": "N", "characteristic": "c", "soul": "s",
        "form": "bluf", "default_voice_level": 5}]}, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("HARNESS_PERSONA_BUNDLES", str(reg))
    outp = tmp_path / "output.yaml"
    outp.write_text("language: vi\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_OUTPUT", str(outp))
    tv = tmp_path / "terminal-voice.yaml"
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(tv))
    import inject_prompt_context as ipc
    tv.write_text("persona_bundle: null\nvoice_level: 5\n", encoding="utf-8")
    sig_off = ipc._context_sig(tmp_path)
    tv.write_text("persona_bundle: b\nvoice_level: 5\n", encoding="utf-8")
    sig_on = ipc._context_sig(tmp_path)
    assert sig_off != sig_on


def test_subagent_surface_still_excludes_pinned_name(tmp_path, monkeypatch):
    # The slim pin is MAIN-only; the subagent surface must STILL carry ZERO name/gist.
    _setup_active_bundle(tmp_path, monkeypatch, with_pii=False)
    import subagent_init
    txt = subagent_init.context_text({"agent_type": "tester"})
    assert NAME_S not in txt
    assert CHAR_S not in txt
