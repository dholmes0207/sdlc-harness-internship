"""test_voice_presets.py — voice-presets.yaml + voice_presets.py + setup onboarding.

TDD red phase: tests written before implementation.
"""

import sys
import tempfile
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DATA = Path(__file__).resolve().parents[1] / "data"
SKILLS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills"

sys.path.insert(0, str(SCRIPTS))

VOICE_PRESETS_YAML = DATA / "voice-presets.yaml"
OBSERVATION_SIGNALS_YAML = DATA / "observation-signals.yaml"
SETUP_SKILL_MD = SKILLS_DIR / "setup" / "SKILL.md"

# The 10 axes in the no-orphan onboarding matrix (from plan §Ma trận cắm-dây)
ALL_AXES = {
    "audience", "code_style", "voice_level", "terminal_voice_level",
    "persona", "no_markdown", "interview_rigor", "action_prompting",
    "language", "humanize",
}
# Axes removed from onboarding
REMOVED_AXES = {"detail_level", "output_style"}


def _load_presets():
    """Load voice-presets.yaml; return the list of presets."""
    assert VOICE_PRESETS_YAML.exists(), "voice-presets.yaml not found"
    with VOICE_PRESETS_YAML.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    assert isinstance(doc, dict) and "presets" in doc, "voice-presets.yaml must have a 'presets' key"
    return doc["presets"]


def test_preset_count_is_ten():
    """Exactly 10 archetype presets (count-coupling is intentional)."""
    presets = _load_presets()
    assert len(presets) == 10, "Expected 10 presets, got %d" % len(presets)


def test_all_presets_validate_against_both_schemas():
    """Every axes value in all 10 presets is valid against the union of
    voice_prefs.ENUMS and output_config constraints (F8 drift test).
    An invalid value must cause this test to FAIL.
    """
    import voice_prefs
    import output_config

    presets = _load_presets()
    for preset in presets:
        pid = preset.get("id", "?")
        axes = preset.get("axes", {})

        # Terminal-voice axes: validate via voice_prefs.ENUMS + _BOOL_KEYS
        for key in axes:
            val = axes[key]
            if key in voice_prefs._BOOL_KEYS:
                assert isinstance(val, bool), (
                    "Preset %r: %r must be bool, got %r" % (pid, key, val))
            elif key in voice_prefs.ENUMS:
                # None is allowed (means "absent/default")
                if val is not None:
                    assert not isinstance(val, bool) and val in voice_prefs.ENUMS[key], (
                        "Preset %r: %r=%r is not in enum %r"
                        % (pid, key, val, sorted(str(x) for x in voice_prefs.ENUMS[key])))
            elif key in ("audience", "code_style"):
                # output_config: 0..5 or None/off
                if val is not None and val is not False:
                    assert isinstance(val, int) and 0 <= val <= 5, (
                        "Preset %r: %r=%r must be 0..5 or None/off" % (pid, key, val))
            elif key == "language":
                if val is not None:
                    assert val in output_config.VALID_LANGUAGES, (
                        "Preset %r: language=%r not in %r" % (pid, val, output_config.VALID_LANGUAGES))
            elif key == "humanize":
                assert isinstance(val, bool), (
                    "Preset %r: humanize must be bool, got %r" % (pid, val))
            # Unknown keys are NOT allowed
            else:
                all_known = (set(voice_prefs.DEFAULTS.keys()) |
                             {"audience", "code_style", "language", "humanize"})
                assert key in all_known, "Preset %r: unknown axis %r" % (pid, key)


def test_apply_all_or_nothing():
    """Simulating a write failure on file-2 must leave file-1 unchanged.
    After a failed apply, both files retain their original values.
    """
    import voice_presets as vp_mod

    with tempfile.TemporaryDirectory() as d:
        tv_yaml = Path(d) / "terminal-voice.yaml"
        out_yaml = Path(d) / "output.yaml"

        # Write initial state
        tv_yaml.write_text("voice_level: 5\npersona: none\nterminal_voice_level: 3\n"
                           "no_markdown: false\ninterview_rigor: standard\naction_prompting: standard\n",
                           encoding="utf-8")
        out_yaml.write_text("language: vi\nhumanize: true\n", encoding="utf-8")

        original_tv = tv_yaml.read_text(encoding="utf-8")

        # Try to apply a preset but inject a failure via a bad output path for file-2
        presets = _load_presets()
        preset = presets[0]  # use first preset

        # Simulate failure by passing a read-only directory as out_yaml path
        bad_out_path = Path(d) / "readonly_dir" / "output.yaml"
        # bad_out_path's parent does not exist → write will fail

        try:
            vp_mod.apply(preset, tv_path=str(tv_yaml), out_path=str(bad_out_path))
        except Exception:
            pass  # Expected to fail

        # file-1 (tv_yaml) must be UNCHANGED
        assert tv_yaml.read_text(encoding="utf-8") == original_tv, (
            "tv_yaml was modified despite apply failure — all-or-nothing violated"
        )


def test_picker_rejects_out_of_range():
    """Entering a number outside [1..10] or a non-number must return a reject signal."""
    import voice_presets as vp_mod

    for bad_input in ("0", "11", "99", "-1", "abc", ""):
        result = vp_mod.pick(bad_input)
        assert result is None or result == "invalid", (
            "pick(%r) should return None/invalid for out-of-range input, got %r"
            % (bad_input, result)
        )


def test_feedback_signal_registered():
    """observation-signals.yaml must contain the audience-plain-reask feedback signal."""
    assert OBSERVATION_SIGNALS_YAML.exists(), "observation-signals.yaml not found"
    content = OBSERVATION_SIGNALS_YAML.read_text(encoding="utf-8")
    assert "audience-plain-reask" in content, (
        "observation-signals.yaml missing 'audience-plain-reask' signal — "
        "required by P9 for measuring audience=plain effectiveness"
    )


def test_setup_full_menu_covers_all_axes():
    """setup/SKILL.md full-menu must list all 10 axes of the no-orphan matrix.
    Must NOT contain the removed axes (detail_level, output_style).
    Thiếu hoặc thừa → FAIL (khoá 'đừng để config nào mồ côi khỏi setup').
    """
    assert SETUP_SKILL_MD.exists(), "setup/SKILL.md not found"
    content = SETUP_SKILL_MD.read_text(encoding="utf-8")

    missing = []
    for axis in ALL_AXES:
        if axis not in content:
            missing.append(axis)
    assert not missing, (
        "setup/SKILL.md missing axes from the no-orphan matrix:\n  " + "\n  ".join(missing)
    )

    for removed in REMOVED_AXES:
        # These must not appear as knob entries in the table (they were renamed/merged)
        # We check the table rows specifically, not prose mentions
        # A simple check: they should not appear as a table cell `| detail_level |` etc.
        assert "| %s |" % removed not in content and "| `%s`" % removed not in content, (
            "setup/SKILL.md still references removed knob %r in a table" % removed
        )


def test_pick_accepts_full_preset_range():
    """pick must accept every loaded preset (bounded by len(load_presets()),
    not a hard-coded 10) and reject one past the end."""
    import voice_presets as vp_mod
    n = len(vp_mod.load_presets())
    assert vp_mod.pick(str(n)) is not None, "last preset rejected"
    assert vp_mod.pick(str(n + 1)) is None, "past-end accepted"


def test_preset_apply_clears_persona_bundle():
    """F2 mutual-exclusion: a preset sets the `persona` form, so applying one must
    clear any active persona_bundle — otherwise the bundle would keep absorbing the
    persona knob and the preset's form would silently lose. Preset form wins."""
    import voice_presets as vp_mod
    import voice_prefs
    with tempfile.TemporaryDirectory() as d:
        tv = Path(d) / "terminal-voice.yaml"
        out = Path(d) / "output.yaml"
        out.write_text("language: vi\n", encoding="utf-8")  # valid output config to merge over
        # a bundle is active on disk before the preset is applied
        voice_prefs.save({"persona_bundle": "stub-example", "voice_level": 6}, path=str(tv))
        preset = _load_presets()[0]  # any preset — it carries a `persona` axis
        vp_mod.apply(preset, tv_path=str(tv), out_path=str(out))
        assert voice_prefs.load(str(tv))["persona_bundle"] is None
