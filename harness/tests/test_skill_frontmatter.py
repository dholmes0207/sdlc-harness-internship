"""test_skill_frontmatter.py — the shared SKILL.md frontmatter reader.

The eight hand-rolled parsers now delegate here, so this pins the contract they all
depend on: tolerant fence detection (both `---` and `...` close), folded-scalar-safe
description, fail-soft {}/"" on malformed input.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import skill_frontmatter as sf  # noqa: E402


def test_frontmatter_parses_dict():
    md = "---\nname: hs:x\ndescription: Do a thing. Use when needed.\n---\n# Body\n"
    fm = sf.frontmatter(md)
    assert fm["name"] == "hs:x"
    assert fm["description"] == "Do a thing. Use when needed."


def test_dots_close_is_honored_and_body_not_folded():
    md = "---\nname: hs:x\n...\n\nBody mentions compliance-tier: bogus\n"
    block, body = sf.split(md)
    assert "name: hs:x" in block and "bogus" not in block
    assert "bogus" in body
    assert sf.frontmatter(md)["name"] == "hs:x"


def test_no_leading_fence_is_empty():
    assert sf.frontmatter("# just a heading\nno fm\n") == {}
    assert sf.body("# h\n") == "# h\n"


def test_no_closing_fence_yields_empty_dict_and_whole_body():
    md = "---\nname: hs:x\nno close here\n"
    assert sf.frontmatter(md) == {}
    assert sf.body(md) == md  # whole text is body when the block never closes


def test_folded_description_is_joined():
    md = "---\nname: hs:x\ndescription: >-\n  Manage the budget.\n  Use when full.\n---\n# B\n"
    desc = sf.description(md)
    assert "Manage the budget." in desc and "Use when full." in desc


def test_body_after_frontmatter():
    md = "---\nname: hs:x\n---\n# Title\n\nline\n"
    assert sf.body(md) == "# Title\n\nline\n"


def test_regex_fallback_when_yaml_missing(monkeypatch):
    monkeypatch.setattr(sf, "_YAML", False)
    md = '---\nname: hs:x\ndescription: "quoted value"\n---\n# B\n'
    assert sf.frontmatter(md)["description"] == "quoted value"
    assert sf.description(md) == "quoted value"


def test_regex_fallback_collapses_folded_to_indicator(monkeypatch):
    # documented degraded shape: without yaml a folded value is just the `>` token
    monkeypatch.setattr(sf, "_YAML", False)
    md = "---\nname: hs:x\ndescription: >\n  FOLDED\n---\n# B\n"
    assert sf.description(md) == ">"


def test_non_mapping_block_is_empty_dict():
    assert sf.frontmatter("---\n- a\n- b\n---\n# B\n") == {}
