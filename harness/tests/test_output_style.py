"""test_output_style.py — the code_style audience-adaptation axis.

`code_style` (0..5) was renamed from `output_style` and MOVED out of
terminal-voice.yaml into output.yaml (owned by output_config). It adapts the
GENERATED CODE itself — comment density, verbosity, examples — to the reader's
coding expertise (0=absolute beginner … 5=expert). Unlike the terminal-voice
knobs it is deliberately NOT scope-fenced: it shapes the deliverable. Default is
off (None). voice_prefs keeps only the level→profile data + resolver; the knob
VALUE lives in output.yaml. A legacy shim maps an old `output_style` key to
`code_style` until the project migrates.
"""
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
_HOOKS = _ROOT / "harness" / "hooks"
for p in (_SCRIPTS, _HOOKS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import voice_prefs  # noqa: E402
import voice_inject  # noqa: E402
import output_config  # noqa: E402

_STYLE_DIR = _ROOT / "harness" / "data" / "output-styles"
_LEVELS = {0: "eli5", 1: "junior", 2: "mid", 3: "senior", 4: "lead", 5: "god"}


def test_six_profiles_present_and_brand_clean():
    import re
    banned = re.compile(r"/ck:|\.claude/" + r"(?:skills|hooks)/|ClaudeKit|claudekit", re.I)
    for lvl, name in _LEVELS.items():
        f = _STYLE_DIR / ("code-style-level-%d.md" % lvl)
        assert f.is_file(), "missing profile %s" % f
        assert not banned.search(f.read_text(encoding="utf-8")), "brand leak in %s" % f


def test_profile_resolver():
    prof = voice_prefs.code_style_profile(3)
    assert prof is not None
    assert prof["name"] == "senior"
    assert Path(prof["file"]).is_file()
    assert voice_prefs.code_style_profile(None) is None


def test_injection_includes_block_when_set():
    ctx = voice_inject.build_context({**voice_prefs.DEFAULTS, "code_style": 0})
    assert "code style" in ctx.lower()
    assert "eli5" in ctx.lower()
    # it must declare that this axis DOES shape the deliverable (not scope-fenced)
    assert "harness/data/output-styles/" in ctx


def test_injection_omits_block_when_off():
    ctx = voice_inject.build_context(dict(voice_prefs.DEFAULTS))  # no code_style
    assert "output-styles/" not in ctx


# ---- P3: rename + move ownership (terminal-voice.yaml → output.yaml) + shim ----

def test_voice_prefs_drops_output_style(tmp_path):
    # voice_prefs no longer OWNS the knob: gone from DEFAULTS/ENUMS, and the value
    # does NOT relocate into voice_prefs under the new name either (it lives in
    # output.yaml). `--set output_style=` must be rejected as an unknown knob.
    assert "output_style" not in voice_prefs.DEFAULTS
    assert "output_style" not in voice_prefs.ENUMS
    assert "code_style" not in voice_prefs.DEFAULTS
    vp = _SCRIPTS / "voice_prefs.py"
    cfg = tmp_path / "terminal-voice.yaml"
    env = {**os.environ, "HARNESS_TERMINAL_VOICE": str(cfg)}
    r = subprocess.run([sys.executable, str(vp), "--set", "output_style=3"],
                       env=env, capture_output=True, text=True)
    assert r.returncode != 0, "stale output_style knob still accepted"


def test_code_style_owned_by_output_yaml(tmp_path):
    oc = _SCRIPTS / "output_config.py"
    out = tmp_path / "output.yaml"
    out.write_text("language: vi\nhumanize: true\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(oc), "--file", str(out), "--set", "code_style=5"],
        env={**os.environ}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert output_config.load_output(path=str(out))["code_style"] == 5


def test_legacy_output_style_shim(tmp_path):
    # old install: terminal-voice.yaml still carries output_style; output.yaml has
    # no code_style yet → resolve_all maps legacy → code_style + deprecation diag.
    tv = tmp_path / "terminal-voice.yaml"
    tv.write_text("persona: none\noutput_style: 3\n", encoding="utf-8")
    out = tmp_path / "output.yaml"
    out.write_text("language: vi\nhumanize: true\n", encoding="utf-8")
    merged = output_config.resolve_all(voice_path=str(tv), output_path=str(out))
    assert merged["code_style"] == 3
    diag = " ".join(merged.get("_diag", [])) + " " + str(merged.get("_voice_diag", ""))
    assert "output_style" in diag.lower() or "deprecat" in diag.lower()
    # when output.yaml DOES set code_style, it wins over the legacy shim
    out.write_text("language: vi\ncode_style: 1\n", encoding="utf-8")
    merged2 = output_config.resolve_all(voice_path=str(tv), output_path=str(out))
    assert merged2["code_style"] == 1
