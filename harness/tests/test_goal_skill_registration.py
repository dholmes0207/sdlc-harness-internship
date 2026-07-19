"""test_goal_skill_registration.py — hs:goal is a registered flow skill.

hs:goal is the authoring-time wrapper for a built-in /goal run: it prepares a
self-contained goal.md, arms hs:autonomous-bell, scaffolds the cycle dir, then
hands the loop to built-in /goal. It is a NON-core flow skill that depends on
autonomous-bell (the stop substrate it arms). The hard `hs:autonomous-bell`
route in its prose must be mirrored into skill-deps.yaml (handoff-deps drift).
"""
from pathlib import Path
import pytest

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_DATA = _ROOT / "harness" / "data"
_SKILL = _ROOT / "harness" / "plugins" / "hs" / "skills" / "goal" / "SKILL.md"


def _load(name):
    return yaml.safe_load((_DATA / name).read_text(encoding="utf-8"))


def test_goal_in_flow_components():
    comp = _load("components.yaml")
    assert "goal" in comp["components"]["flow"]["skills"]


def test_goal_in_decomposition_map_as_flow():
    decomp = _load("decomposition-map.yaml")
    assert decomp["skills"].get("goal") == "flow"


def test_goal_deps_autonomous_bell_non_core():
    deps = _load("skill-deps.yaml")
    assert "goal" in deps["skills"]
    assert deps["skills"]["goal"]["deps"] == ["autonomous-bell"]
    assert "goal" not in deps["core_immutable"]


@pytest.mark.dev_repo
def test_goal_skill_md_exists_with_frontmatter():
    text = _SKILL.read_text(encoding="utf-8")
    assert text.startswith("---")
    fm = yaml.safe_load(text.split("---", 2)[1])
    assert fm["name"] == "hs:goal"  # hs:-prefixed (invocation follows frontmatter name)


@pytest.mark.dev_repo
def test_goal_skill_states_builtin_boundary():
    text = _SKILL.read_text(encoding="utf-8")
    # the authoring-only boundary must be visible: generates a NEW goal.md,
    # never edits an existing ephemeral one
    assert "new" in text.lower() and "goal.md" in text
    assert "autonomous-bell" in text  # hard route mirrored to deps
