"""test_agentize_delta.py — agentize teaches the generated tool to advertise its companion skill.

The output contract gains companion-skill DISCOVERABILITY: the generated CLI `--help` carries an
`Agent Skill: <name>` hint, the MCP server exposes a `skill://<name>` resource + a
`use-<tool>-skill` prompt (or a hint in tool descriptions), the README carries an agent-facing
note, and a test/fixture proves the hint is present. Companion paths stay harness-native
(`harness/plugins/hs/skills/<tool>/`), never the install-output tree. Red before the edits.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SKILL = _ROOT / "harness" / "plugins" / "hs" / "skills" / "agentize" / "SKILL.md"

_DEAD_SKILLS = "." + "claude" + "/skills/"
_DEAD_HOOKS = "." + "claude" + "/hooks/"
_DEAD_BRAND = "claude" + "kit"


def test_companion_hint_documented():
    body = _SKILL.read_text(encoding="utf-8")
    assert "companion" in body.lower(), "companion skill not mentioned"
    # CLI --help hint
    assert "Agent Skill:" in body, "missing the CLI --help 'Agent Skill:' hint"
    # MCP resource + prompt discoverability
    assert "skill://" in body, "missing the MCP skill:// resource hint"
    assert re.search(r"use-<?tool>?-skill|use-\w+-skill", body), "missing the use-<tool>-skill MCP prompt"
    # a test/fixture must prove the hint is emitted
    assert re.search(r"fixture|prov(e|ing)", body, re.IGNORECASE), \
        "no test/fixture requirement proving the companion hint is emitted"


def test_no_source_brand_leak():
    low = _SKILL.read_text(encoding="utf-8").lower()
    assert not re.search(r"\bck:", low), "surviving ck: route in agentize SKILL"
    assert _DEAD_BRAND not in low, "source brand survived in agentize SKILL"
    assert _DEAD_SKILLS not in low, "install-output skills path in agentize SKILL"
    assert _DEAD_HOOKS not in low, "install-output hooks path in agentize SKILL"
