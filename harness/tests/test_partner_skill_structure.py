"""hs:partner SKILL.md structural contract.

Twin check of test_gemini_skill_structure.py, scoped to what phase 6 wires:
frontmatter name (invocation follows the frontmatter name, not the dir) and
the CI HC-1 ban on a literal install-output path string inside harness/.
"""
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_SKILL = _ROOT / "plugins" / "hs" / "skills" / "partner"


def _frontmatter(md_path):
    text = md_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fm = text.split("---\n", 2)[1]
    return yaml.safe_load(fm)


def test_frontmatter_name_is_hs_partner():
    fm = _frontmatter(_SKILL / "SKILL.md")
    assert fm["name"] == "hs:partner"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()
    assert fm.get("injectable") is False
    assert fm.get("metadata", {}).get("compliance-tier") == "workflow"


def test_no_claude_path_strings():
    # Build the banned substrings from fragments so this very file does not
    # trip the HC-1 grep guard it exists to enforce (a literal here would be a
    # tracked false positive).
    text = (_SKILL / "SKILL.md").read_text(encoding="utf-8")
    runtime = "." + "claude/"
    assert runtime + "skills/" not in text
    assert runtime + "hooks/" not in text
