"""test_vibe_skill_registered.py — hs:vibe is a real, fully-wired orchestrator skill.

A new skill is not "done" until the registry knows it: skill-deps.yaml (dep edges),
components.yaml (group label), decomposition-map.yaml (group), and a valid thin-core
SKILL.md. These pin the wiring so a half-registered skill trips the suite. Red before
the skill + wiring exist.
"""
import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SKILL = _ROOT / "harness" / "plugins" / "hs" / "skills" / "vibe" / "SKILL.md"
_DEPS = _ROOT / "harness" / "data" / "skill-deps.yaml"
_COMPONENTS = _ROOT / "harness" / "data" / "components.yaml"
_DECOMP = _ROOT / "harness" / "data" / "decomposition-map.yaml"

# vibe chains the spine; these are the routing edges the orchestrator depends on.
_EXPECTED_DEPS = {
    "worktree", "plan", "cook", "fix", "code-review", "ship", "review-pr", "git",
}


def _frontmatter(md: Path) -> dict:
    text = md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "SKILL.md missing YAML frontmatter"
    return yaml.safe_load(m.group(1))


@pytest.mark.dev_repo
def test_skill_file_present_with_valid_frontmatter():
    assert _SKILL.exists(), "vibe/SKILL.md missing"
    fm = _frontmatter(_SKILL)
    assert fm["name"] == "hs:vibe", "frontmatter name must be 'hs:vibe'"
    assert fm.get("argument-hint"), "vibe takes args (issue url / request + flags)"
    assert isinstance(fm.get("description"), str) and len(fm["description"]) >= 30


def test_deps_declare_the_spine_chain():
    data = yaml.safe_load(_DEPS.read_text(encoding="utf-8"))
    assert "vibe" in data["skills"], "vibe missing from skill-deps.yaml"
    deps = set(data["skills"]["vibe"].get("deps", []))
    missing = _EXPECTED_DEPS - deps
    assert not missing, f"vibe deps missing the spine edges: {sorted(missing)}"


def test_vibe_not_in_core_immutable():
    data = yaml.safe_load(_DEPS.read_text(encoding="utf-8"))
    assert "vibe" not in data["core_immutable"], "vibe is an opt-in orchestrator, not spine"


def test_components_and_decomposition_list_vibe_under_flow():
    comps = yaml.safe_load(_COMPONENTS.read_text(encoding="utf-8"))["components"]
    assert "vibe" in comps["flow"]["skills"], "vibe missing from components.yaml flow group"
    dm = yaml.safe_load(_DECOMP.read_text(encoding="utf-8"))
    assert dm["skills"].get("vibe") == "flow", "vibe missing/mis-grouped in decomposition-map.yaml"


@pytest.mark.dev_repo
def test_body_is_thin_core():
    body = _SKILL.read_text(encoding="utf-8").split("---\n", 2)[-1]
    assert len(body.splitlines()) <= 200, "vibe SKILL.md body exceeds the thin-core ceiling"
