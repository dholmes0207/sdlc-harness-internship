"""test_agy_mcp_path.py — agy-MCP is documented as an independent path, gemini stays primary.

The use-mcp integration guide gains a self-contained "agy-MCP (independent path)" section that
records the facts we were missing (agy loads AGENTS.md from the workspace, NOT GEMINI.md; the
global mcp_config.json is empty by default and must be populated before agy sees any MCP). The
reframe must NOT regress the locked dual-CLI stance: gemini + GEMINI_API_KEY stays the PRIMARY
headless/CI CLI (the 2026-06-18 change cut only the consumer OAuth tiers). Red before the edits.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_REF = _ROOT / "harness" / "plugins" / "hs" / "skills" / "use-mcp" / "references" / "llm-cli-integration.md"
_SKILL = _ROOT / "harness" / "plugins" / "hs" / "skills" / "use-mcp" / "SKILL.md"

_DEAD_SKILLS = "." + "claude" + "/skills/"
_DEAD_HOOKS = "." + "claude" + "/hooks/"
_DEAD_BRAND = "claude" + "kit"


def test_agents_md_fact_present():
    body = _REF.read_text(encoding="utf-8")
    assert "AGENTS.md" in body, "missing the agy AGENTS.md fact"
    assert "GEMINI.md" in body, "does not distinguish AGENTS.md from the retired GEMINI.md"


def test_agy_independent_section_exists():
    ref = _REF.read_text(encoding="utf-8")
    assert re.search(r"^#+\s*agy-MCP \(independent", ref, re.MULTILINE | re.IGNORECASE), \
        "no self-contained 'agy-MCP (independent path)' section in the integration guide"
    # the empty-config operational note must be present so we do not over-claim "agy MCP just works"
    assert "mcp_config.json" in ref and re.search(r"empty|rỗng", ref, re.IGNORECASE), \
        "missing the empty mcp_config.json operational note"
    # the SKILL Path 1 must frame agy as an independent path, not merely a fallback
    skill = _SKILL.read_text(encoding="utf-8")
    assert re.search(r"independent", skill, re.IGNORECASE), \
        "use-mcp SKILL Path 1 does not frame agy as an independent path"


def test_keeps_gemini_primary():
    body = _REF.read_text(encoding="utf-8")
    assert "consumer OAuth tiers" in body, "lost the 2026-06-18 consumer-tier scoping"
    assert re.search(r"gemini[^\n]*primary|primary[^\n]*gemini", body, re.IGNORECASE), \
        "gemini is no longer marked primary — regression of the locked dual-CLI stance"


def test_no_source_brand_leak():
    for f in (_REF, _SKILL):
        low = f.read_text(encoding="utf-8").lower()
        assert not re.search(r"\bck:", low), f"surviving ck: route in {f.name}"
        assert _DEAD_BRAND not in low, f"source brand survived in {f.name}"
        assert _DEAD_SKILLS not in low, f"install-output skills path in {f.name}"
        assert _DEAD_HOOKS not in low, f"install-output hooks path in {f.name}"
