"""test_voice_prefs.py — the terminal-voice knob loader/saver.

One tolerant config home for the conversational-register knobs (terminal_voice_level,
persona, voice_level, no_markdown). The read path NEVER raises: a missing file,
missing key, out-of-range enum, wrong-typed value, or corrupt YAML all degrade
to the hard-coded default. The write path validates the closed enums and raises
VoicePrefsError so the on-disk file stays canonical for the next read.

Mirrors product-spec preferences.py tolerance; loader idiom (env override for a
scratch file) mirrors guard_policy. Pure resolution is exercised by direct
import; the CLI seam is driven in-process via HARNESS_TERMINAL_VOICE.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import voice_prefs  # noqa: E402


# ----------------------------------------------------------------- helpers ---

def _write(tmp_path, doc):
    """Write a terminal-voice.yaml under tmp_path and return its path."""
    import yaml

    p = tmp_path / "terminal-voice.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return p


# ------------------------------------------------------------- load defaults ---

def test_defaults_when_file_absent(tmp_path):
    got = voice_prefs.load(tmp_path / "nope.yaml")
    assert got == {
        "terminal_voice_level": 5,
        "persona": "none",
        "voice_level": 5,
        "no_markdown": False,
        "interview_rigor": "standard",
        "action_prompting": "standard",
        "persona_bundle": None,
    }


def test_valid_file_resolves(tmp_path):
    p = _write(tmp_path, {
        "terminal_voice_level": 2,
        "persona": "none",
        "voice_level": 7,
        "no_markdown": True,
    })
    got = voice_prefs.load(p)
    assert got["terminal_voice_level"] == 2
    assert got["voice_level"] == 7
    assert got["no_markdown"] is True


def test_partial_file_fills_defaults(tmp_path):
    p = _write(tmp_path, {"voice_level": 3})
    got = voice_prefs.load(p)
    assert got["voice_level"] == 3
    assert got["terminal_voice_level"] == 5  # default filled
    assert got["persona"] == "none"


# ------------------------------------------------------- out-of-range / typo ---

def test_terminal_voice_level_out_of_range_falls_back(tmp_path):
    assert voice_prefs.load(_write(tmp_path, {"terminal_voice_level": 9}))["terminal_voice_level"] == 5
    assert voice_prefs.load(_write(tmp_path, {"terminal_voice_level": -1}))["terminal_voice_level"] == 5


def test_voice_level_out_of_range_falls_back(tmp_path):
    assert voice_prefs.load(_write(tmp_path, {"voice_level": 0}))["voice_level"] == 5
    assert voice_prefs.load(_write(tmp_path, {"voice_level": 10}))["voice_level"] == 5


def test_unknown_persona_falls_back_to_none(tmp_path):
    # Phase 1 enum is {none}; the 13 catalog ids land in phase 3.
    assert voice_prefs.load(_write(tmp_path, {"persona": "wizard"}))["persona"] == "none"


def test_bool_cannot_sneak_into_int_enum(tmp_path):
    # True == 1 / False == 0 in Python; a level is an int, never a bool. A
    # hand-typed `terminal_voice_level: true` must fall back, not resolve to 1.
    assert voice_prefs.load(_write(tmp_path, {"terminal_voice_level": True}))["terminal_voice_level"] == 5
    assert voice_prefs.load(_write(tmp_path, {"voice_level": False}))["voice_level"] == 5


def test_non_bool_no_markdown_falls_back(tmp_path):
    assert voice_prefs.load(_write(tmp_path, {"no_markdown": "maybe"}))["no_markdown"] is False
    assert voice_prefs.load(_write(tmp_path, {"no_markdown": 3}))["no_markdown"] is False


# --------------------------------------------------------------- corruption ---

def test_corrupt_yaml_degrades_to_defaults(tmp_path):
    p = tmp_path / "terminal-voice.yaml"
    p.write_text("terminal_voice_level: [unterminated\n:::", encoding="utf-8")
    got = voice_prefs.load(p)  # must NOT raise
    assert got["terminal_voice_level"] == 5


def test_non_mapping_top_level_degrades(tmp_path):
    p = tmp_path / "terminal-voice.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert voice_prefs.load(p) == dict(voice_prefs.DEFAULTS)


# ----------------------------------------------------------------- env seam ---

def test_env_override_honored(tmp_path, monkeypatch):
    p = _write(tmp_path, {"voice_level": 8})
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(p))
    assert voice_prefs.load()["voice_level"] == 8
    assert voice_prefs.voice_path() == p


def test_explicit_path_beats_env(tmp_path, monkeypatch):
    env_file = _write(tmp_path, {"voice_level": 8})
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(env_file))
    other = tmp_path / "other.yaml"
    import yaml
    other.write_text(yaml.safe_dump({"voice_level": 2}), encoding="utf-8")
    assert voice_prefs.load(other)["voice_level"] == 2


# -------------------------------------------------------- dev-override seam ---
# A gitignored .harness-dev/terminal-voice.yaml at the repo root lets THIS dev
# run a different TERMINAL posture without editing the committed default (which
# must ship safe). Discovered AFTER the env seam, BEFORE the shipped default, so
# the gate-neutral conversational voice is live-without-restart while env still
# wins for tests / power users. Gate-AFFECTING config (guard/stage) is NOT
# file-discovered — it stays env-bound so the pre-push HARNESS_* scrub governs
# it; only the scope-fenced voice is discoverable.

def test_dev_override_discovered_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_TERMINAL_VOICE", raising=False)
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    dev = tmp_path / ".harness-dev"
    dev.mkdir()
    (dev / "terminal-voice.yaml").write_text(
        "voice_level: 9\npersona: reality-check\n", encoding="utf-8")
    got = voice_prefs.load()
    assert got["voice_level"] == 9
    assert got["persona"] == "reality-check"
    assert voice_prefs.voice_path() == dev / "terminal-voice.yaml"


def test_env_seam_wins_over_dev_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    dev = tmp_path / ".harness-dev"
    dev.mkdir()
    (dev / "terminal-voice.yaml").write_text("voice_level: 9\n", encoding="utf-8")
    env_file = tmp_path / "env-voice.yaml"
    env_file.write_text("voice_level: 2\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_TERMINAL_VOICE", str(env_file))
    assert voice_prefs.load()["voice_level"] == 2


def test_shipped_default_when_no_dev_override(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_TERMINAL_VOICE", raising=False)
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))   # tmp has no .harness-dev
    assert voice_prefs.voice_path() == voice_prefs._VOICE_DEFAULT


# --------------------------------------------------------------------- save ---

def test_save_round_trips(tmp_path):
    p = tmp_path / "out.yaml"
    voice_prefs.save({"terminal_voice_level": 1, "voice_level": 9,
                      "persona": "none", "no_markdown": True}, p)
    got = voice_prefs.load(p)
    assert got["terminal_voice_level"] == 1
    assert got["voice_level"] == 9
    assert got["no_markdown"] is True


def test_save_preserves_leading_comment_header(tmp_path):
    # the header documents the knob ranges; a --set rewrite must keep it, like
    # the sibling config CLIs (output/guard/team) — not silently destroy it.
    p = tmp_path / "out.yaml"
    p.write_text(
        "# terminal-voice.yaml — register doc header\n"
        "# voice_level: 1..9\n"
        "voice_level: 5\n", encoding="utf-8")
    voice_prefs.save({"voice_level": 9}, p)
    text = p.read_text(encoding="utf-8")
    assert "# terminal-voice.yaml — register doc header" in text
    assert "# voice_level: 1..9" in text
    assert voice_prefs.load(p)["voice_level"] == 9  # value still updated


def test_save_drops_unknown_keys(tmp_path):
    p = tmp_path / "out.yaml"
    voice_prefs.save({"voice_level": 5, "bogus": "x"}, p)
    import yaml
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert "bogus" not in raw


def test_save_rejects_out_of_enum(tmp_path):
    with pytest.raises(voice_prefs.VoicePrefsError):
        voice_prefs.save({"voice_level": 12}, tmp_path / "out.yaml")
    with pytest.raises(voice_prefs.VoicePrefsError):
        voice_prefs.save({"terminal_voice_level": 7}, tmp_path / "out.yaml")


def test_save_rejects_non_bool_no_markdown(tmp_path):
    with pytest.raises(voice_prefs.VoicePrefsError):
        voice_prefs.save({"no_markdown": "yes"}, tmp_path / "out.yaml")


def test_save_rejects_bool_for_int_enum(tmp_path):
    with pytest.raises(voice_prefs.VoicePrefsError):
        voice_prefs.save({"voice_level": True}, tmp_path / "out.yaml")


# ---------------------------------------------------------------------- CLI ---

def _cli(tmp_path, *args):
    import os
    env = dict(os.environ)
    env["HARNESS_TERMINAL_VOICE"] = str(tmp_path / "terminal-voice.yaml")
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "voice_prefs.py"), *args],
        capture_output=True, text=True, env=env,
    )


def test_cli_dump_defaults(tmp_path):
    proc = _cli(tmp_path)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["voice_level"] == 5


def test_cli_set_persists(tmp_path):
    assert _cli(tmp_path, "--set", "voice_level=7").returncode == 0
    proc = _cli(tmp_path)
    assert json.loads(proc.stdout)["voice_level"] == 7


def test_cli_set_unknown_key_exits_nonzero(tmp_path):
    proc = _cli(tmp_path, "--set", "bogus=1")
    assert proc.returncode != 0


def test_cli_set_out_of_enum_exits_nonzero(tmp_path):
    proc = _cli(tmp_path, "--set", "voice_level=99")
    assert proc.returncode != 0


# --------------------------------------------------------------- persona catalog ---

_RULES = Path(__file__).resolve().parent.parent / "rules"
_VOICE_SKILL = (Path(__file__).resolve().parent.parent / "plugins" / "hs"
                / "skills" / "voice" / "SKILL.md")


def _catalog_ids_from_rule():
    import re
    md = (_RULES / "terminal-voice.md").read_text(encoding="utf-8")
    section = md.split("## Personas", 1)[-1]  # ids only count in the catalog section
    return set(re.findall(r"(?m)^\|\s*`([\w-]+)`\s*\|", section))


def test_persona_enum_has_none_plus_13():
    personas = voice_prefs.ENUMS["persona"]
    assert "none" in personas
    assert len(personas) == 14  # none + 8 work + 5 fun
    assert len(voice_prefs.WORK_PERSONAS) == 8
    assert len(voice_prefs.FUN_PERSONAS) == 5
    assert ({"none"} | set(voice_prefs.WORK_PERSONAS)
            | set(voice_prefs.FUN_PERSONAS)) == set(personas)


def test_catalog_ids_match_enum_both_directions():
    # The enum and the markdown catalog are two homes; this guards the drift.
    assert _catalog_ids_from_rule() == set(voice_prefs.ENUMS["persona"])


def test_skill_menu_lists_every_persona():
    skill = _VOICE_SKILL.read_text(encoding="utf-8")
    for pid in voice_prefs.ENUMS["persona"]:
        assert pid in skill, f"persona {pid!r} missing from /hs:voice menu"


def test_valid_persona_resolves(tmp_path):
    assert voice_prefs.load(_write(tmp_path, {"persona": "yoda"}))["persona"] == "yoda"


def test_save_accepts_catalog_persona(tmp_path):
    voice_prefs.save({"persona": "feynman"}, tmp_path / "out.yaml")  # must not raise


# --------------------------------------------------------- interview-rigor knobs ---

def test_interview_knob_defaults(tmp_path):
    got = voice_prefs.load(tmp_path / "nope.yaml")
    assert got["interview_rigor"] == "standard"
    assert got["action_prompting"] == "standard"


def test_interview_knobs_resolve(tmp_path):
    p = _write(tmp_path, {
        "interview_rigor": "deep",
        "action_prompting": "proactive",
    })
    got = voice_prefs.load(p)
    assert got["interview_rigor"] == "deep"
    assert got["action_prompting"] == "proactive"


def test_interview_knob_out_of_enum_falls_back(tmp_path):
    assert voice_prefs.load(
        _write(tmp_path, {"interview_rigor": "extreme"}))["interview_rigor"] == "standard"


# ----------------------------------------- detail_level → terminal_voice_level ---

def test_detail_level_shim_maps_to_tvl(tmp_path):
    # legacy config: detail_level present, no explicit terminal_voice_level →
    # map the old verbosity onto the level scale + flag deprecation.
    got = voice_prefs.load(_write(tmp_path, {"detail_level": "verbose"}))
    assert got["terminal_voice_level"] == 5
    assert "detail_level" not in got  # the knob is gone from the schema
    assert any("detail_level" in d for d in got.get("_diag", []))


def test_explicit_tvl_wins_over_legacy_detail(tmp_path):
    got = voice_prefs.load(_write(tmp_path, {
        "terminal_voice_level": 2, "detail_level": "verbose"}))
    assert got["terminal_voice_level"] == 2  # explicit wins, shim does not override


def test_detail_level_dropped_from_schema(tmp_path):
    import os
    import subprocess
    assert "detail_level" not in voice_prefs.DEFAULTS
    assert "detail_level" not in voice_prefs.ENUMS
    vp = _SCRIPTS / "voice_prefs.py"
    cfg = tmp_path / "terminal-voice.yaml"
    env = {**os.environ, "HARNESS_TERMINAL_VOICE": str(cfg)}
    r = subprocess.run([sys.executable, str(vp), "--set", "detail_level=verbose"],
                       env=env, capture_output=True, text=True)
    assert r.returncode != 0, "stale detail_level knob still accepted"


def test_save_rejects_bad_interview_knob(tmp_path):
    with pytest.raises(voice_prefs.VoicePrefsError):
        voice_prefs.save({"interview_rigor": "extreme"}, tmp_path / "out.yaml")


# ------------------------------------------------------- persona_bundle key ---
#
# persona_bundle is NOT a closed enum (its ids are dynamic, resolved from the
# persona-bundles registry). It rides the tolerant read path (pass-through) and
# the verbatim write path (save persists it, never validates), while validation
# lives at the SETTER (apply_bundle + CLI). This split is what stops a stale
# on-disk id from bricking every other voice write (F1).

_BUNDLES_ENV = "HARNESS_PERSONA_BUNDLES"


def _write_bundles(tmp_path, bundles):
    import yaml
    p = tmp_path / "persona-bundles.yaml"
    p.write_text(yaml.safe_dump({"bundles": bundles}, allow_unicode=True), encoding="utf-8")
    return p


def _fixture_bundle(bid="warm-fixture", lvl=4):
    return {"id": bid, "name": "Fix", "characteristic": "c", "soul": "s",
            "form": "reality-check", "default_voice_level": lvl}


def test_default_persona_bundle_is_none(tmp_path):
    assert voice_prefs.load(tmp_path / "nope.yaml")["persona_bundle"] is None


def test_roundtrip_persona_bundle_id(tmp_path):
    p = tmp_path / "terminal-voice.yaml"
    voice_prefs.save({"persona_bundle": "stub-example"}, path=p)
    assert voice_prefs.load(p)["persona_bundle"] == "stub-example"


def test_roundtrip_persona_bundle_null(tmp_path):
    p = tmp_path / "terminal-voice.yaml"
    voice_prefs.save({"persona_bundle": None}, path=p)
    assert voice_prefs.load(p)["persona_bundle"] is None


def test_save_preserves_unknown_bundle_id_no_raise(tmp_path):
    # F1 anti-brick: a stale on-disk id + a save that changes ANOTHER key must
    # not raise and must keep the id verbatim (validation is at the setter, not save).
    p = tmp_path / "terminal-voice.yaml"
    voice_prefs.save({"persona_bundle": "warm", "voice_level": 7}, path=p)  # no raise
    got = voice_prefs.load(p)
    assert got["persona_bundle"] == "warm"
    assert got["voice_level"] == 7


def test_load_unknown_bundle_id_tolerant(tmp_path):
    p = _write(tmp_path, {"persona_bundle": "xxx"})
    assert voice_prefs.load(p)["persona_bundle"] == "xxx"  # tolerant read, resolve→None later


def test_apply_bundle_unknown_id_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(_BUNDLES_ENV, str(_write_bundles(tmp_path, [_fixture_bundle()])))
    tv = tmp_path / "terminal-voice.yaml"
    with pytest.raises(voice_prefs.VoicePrefsError):
        voice_prefs.apply_bundle("nope", tv_path=tv)
    assert not tv.exists()  # setter rejected BEFORE any write


def test_apply_bundle_seeds_voice_level(tmp_path, monkeypatch):
    monkeypatch.setenv(_BUNDLES_ENV, str(_write_bundles(tmp_path, [_fixture_bundle(lvl=4)])))
    tv = tmp_path / "terminal-voice.yaml"
    voice_prefs.apply_bundle("warm-fixture", tv_path=tv)
    got = voice_prefs.load(tv)
    assert got["persona_bundle"] == "warm-fixture"
    assert got["voice_level"] == 4  # seeded from the bundle's default_voice_level


def test_apply_bundle_last_writer_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(_BUNDLES_ENV, str(_write_bundles(tmp_path, [_fixture_bundle(lvl=4)])))
    tv = tmp_path / "terminal-voice.yaml"
    voice_prefs.apply_bundle("warm-fixture", tv_path=tv)
    cur = voice_prefs.load(tv)
    cur["voice_level"] = 3  # user overrides AFTER applying the bundle
    voice_prefs.save(cur, path=tv)
    got = voice_prefs.load(tv)
    assert got["voice_level"] == 3  # bundle does NOT re-seed voice_level on load
    assert got["persona_bundle"] == "warm-fixture"


def test_apply_bundle_none_clears(tmp_path, monkeypatch):
    monkeypatch.setenv(_BUNDLES_ENV, str(_write_bundles(tmp_path, [_fixture_bundle()])))
    tv = tmp_path / "terminal-voice.yaml"
    voice_prefs.apply_bundle("warm-fixture", tv_path=tv)
    voice_prefs.apply_bundle(None, tv_path=tv)
    assert voice_prefs.load(tv)["persona_bundle"] is None


def test_cli_set_persona_bundle_none_coerces(tmp_path):
    assert _cli(tmp_path, "--set", "persona_bundle=none").returncode == 0
    proc = _cli(tmp_path)
    assert json.loads(proc.stdout)["persona_bundle"] is None


def test_cli_set_unknown_bundle_id_raises(tmp_path):
    # An id absent from the shipped registry is rejected at the CLI setter.
    proc = _cli(tmp_path, "--set", "persona_bundle=zzz-nonexistent-bundle")
    assert proc.returncode != 0
    # nothing persisted: a fresh dump still shows the default None
    assert json.loads(_cli(tmp_path).stdout)["persona_bundle"] is None
