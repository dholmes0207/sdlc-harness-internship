"""Phase-6 wiring/governance contract for the persona bundle.

Light mechanical checks: the two DECs are recorded (via the register CLI), the four
glossary terms exist (via the register CLI), the /hs:voice skill documents the
bundle authoring + set-me + seeded-voice_level echo, and the env is wired
(skip-if-absent, because settings.local.json is gitignored and absent on CI).
"""
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _ROOT / "docs"
_SKILL = _ROOT / "harness" / "plugins" / "hs" / "skills" / "voice" / "SKILL.md"
_SETTINGS = _ROOT / ".claude" / "settings.local.json"


def _decisions_text():
    return (_DOCS / "decisions.yaml").read_text(encoding="utf-8")


def test_dec_249_250_recorded():
    txt = _decisions_text()
    assert "DEC-249" in txt
    assert "DEC-250" in txt
    # no duplicate allocation
    ids = [ln for ln in txt.splitlines() if ln.startswith("- id: DEC-")]
    assert len(ids) == len(set(ids)), "duplicate DEC id in the ledger"


def test_glossary_terms_present():
    txt = (_DOCS / "glossary.yaml").read_text(encoding="utf-8").lower()
    for term in ("persona bundle", "soul", "relationship", "candor-floor"):
        assert term in txt, "glossary missing %r" % term


def test_voice_skill_mentions_bundle():
    md = _SKILL.read_text(encoding="utf-8")
    low = md.lower()
    assert "persona bundle" in low or "persona_bundle" in low
    assert "apply_bundle" in md            # select/clear a bundle
    assert "persona_me" in md              # set-me RELATIONSHIP
    assert "seeded" in low                 # F10: echo the seeded voice_level
    assert "interview" in low              # the meticulous authoring flow


def test_settings_local_has_persona_me_env():
    # F6: settings.local.json is gitignored → absent on CI/fresh machines. Skip
    # rather than fail; only assert the env when the file is actually present.
    if not _SETTINGS.exists():
        pytest.skip("settings.local.json absent (gitignored)")
    data = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    assert "HARNESS_PERSONA_ME" in (data.get("env") or {})
