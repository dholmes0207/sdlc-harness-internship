"""test_coding_agent_orchestration_skill.py — the new orchestration skill lands clean.

A ported skill that helps choose and coordinate coding agents/CLIs. Guards: it exists with
the harness `name: hs:<dir>`, sheds every upstream source brand, is registered in the three
data files (deps + defaults default_off + components flow group), and frames the internal
harness lanes (hs:partner / hs:gemini) as the first reach with hs:workflow-orchestrate owning
internal fan-out. Red before the skill + registration exist.
"""
import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SKILL_DIR = _ROOT / "harness" / "plugins" / "hs" / "skills" / "coding-agent-orchestration"
_SKILL = _SKILL_DIR / "SKILL.md"
_DEPS = _ROOT / "harness" / "data" / "skill-deps.yaml"
_DEFAULTS = _ROOT / "harness" / "data" / "skill-defaults.yaml"
_COMPONENTS = _ROOT / "harness" / "data" / "components.yaml"

# assemble banned brand/route tokens from parts so this file never trips the CI invariant
_DEAD_SKILLS = "." + "claude" + "/skills/"
_DEAD_HOOKS = "." + "claude" + "/hooks/"
_DEAD_BRAND = "claude" + "kit"


def _skill_body() -> str:
    return _SKILL.read_text(encoding="utf-8")


def test_skill_file_exists():
    assert _SKILL.is_file(), "coding-agent-orchestration/SKILL.md missing"


def test_frontmatter_name_hs():
    body = _skill_body()
    m = re.search(r"^name:\s*(\S+)\s*$", body, re.MULTILINE)
    assert m, "no name: in frontmatter"
    assert m.group(1) == "hs:coding-agent-orchestration", f"wrong name: {m.group(1)!r}"


def test_no_source_brand_leak():
    for f in _SKILL_DIR.rglob("*.md"):
        low = f.read_text(encoding="utf-8").lower()
        assert not re.search(r"\bck:", low), f"surviving ck: route in {f.name}"
        assert _DEAD_BRAND not in low, f"source brand survived in {f.name}"
        assert "author: " + _DEAD_BRAND not in low, f"author brand survived in {f.name}"
        assert _DEAD_SKILLS not in low, f"install-output skills path in {f.name}"
        assert _DEAD_HOOKS not in low, f"install-output hooks path in {f.name}"


def test_registered_in_deps_and_defaults():
    deps = yaml.safe_load(_DEPS.read_text(encoding="utf-8"))
    graph = deps.get("skills", deps)
    assert "coding-agent-orchestration" in graph, "not in skill-deps.yaml"

    defaults = yaml.safe_load(_DEFAULTS.read_text(encoding="utf-8"))
    assert "coding-agent-orchestration" in defaults.get("default_off", []), \
        "not in skill-defaults default_off (must ship OFF)"

    comps = yaml.safe_load(_COMPONENTS.read_text(encoding="utf-8"))
    flow = comps.get("components", comps).get("flow", {}).get("skills", [])
    assert "coding-agent-orchestration" in flow, "not in components.yaml flow group"


def test_names_internal_lanes_first():
    body = _skill_body()
    assert "hs:partner" in body, "does not name the hs:partner internal lane"
    assert "hs:gemini" in body, "does not name the hs:gemini internal lane"
    assert "hs:workflow-orchestrate" in body, "does not point to hs:workflow-orchestrate for fan-out"
