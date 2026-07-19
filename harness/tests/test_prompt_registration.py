"""hs:prompt skill registration invariants.

The prompt skill is a default-ON (ship + dev) member of the `extra` group. This pins
the registration surface so a later edit that drops it from a group, flips it OFF, or
strips its skill-deps entry fails loudly.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import catalog  # noqa: E402


def _yaml(rel):
    return yaml.safe_load((ROOT / rel).read_text())


def test_prompt_in_extra_group():
    comps = _yaml("harness/data/components.yaml")
    assert "prompt" in comps["components"]["extra"]["skills"]


def test_prompt_deps_are_the_named_route_doors_only():
    # The discovery list is NEVER hardcoded (enrichment is dynamic via hs:find-skills).
    # Only the fixed named routes the SKILL.md actually points at — the defer-guard's
    # /hs:plan + /hs:find-skills and the /hs:use enable-door for OFF skills — are declared
    # deps (required by test_handoff_deps_drift). The gemini lane is mentioned de-namespaced
    # (advisory), so it is NOT a forced co-install dep.
    deps = _yaml("harness/data/skill-deps.yaml")["skills"]
    assert "prompt" in deps
    assert set((deps["prompt"] or {}).get("deps", [])) == {"find-skills", "plan", "use"}


def test_prompt_ships_on_not_in_default_off():
    off = _yaml("harness/data/skill-defaults.yaml")["default_off"]
    assert "prompt" not in off, "prompt is default-ON on ship"


def test_prompt_dev_on_not_in_dev_off():
    dev_off_path = ROOT / ".harness-dev" / "dev-off-skills.yaml"
    if not dev_off_path.exists():
        return  # dev overlay is machine-local; absence = nothing disabled
    disabled = (yaml.safe_load(dev_off_path.read_text()) or {}).get("disabled") or []
    assert "prompt" not in disabled, "prompt is exposed to this dev session"


def test_prompt_is_owned_by_catalog():
    assert "prompt" in catalog.load_catalog()["owned"]


def test_prompt_skill_dir_and_frontmatter():
    skill = ROOT / "harness/plugins/hs/skills/prompt/SKILL.md"
    assert skill.is_file()
    head = skill.read_text().split("---", 2)[1]
    fm = yaml.safe_load(head)
    assert fm["name"] == "hs:prompt"  # hs:-prefixed (invocation follows frontmatter name)
    assert fm["metadata"]["compliance-tier"] == "workflow"
