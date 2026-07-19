"""Group->skills membership must not drift across its sources.

Post-collapse the same themed grouping is recorded in two files that serve
different roles: decomposition-map.yaml (skill->group, the forward-split map) and
components.yaml (group->skills, the install-time label index read by
skill_selection). They agree today, but nothing else enforces it — moving a skill
in one file alone would silently diverge the installer from the migrator. This
invariant cross-asserts the two for every themed group.
"""
import sys
import pytest
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import component_config as cc  # noqa: E402
import migrate_decomposition as md  # noqa: E402


def test_themed_group_skills_agree_between_components_and_map():
    skill_to_group = md.load_map(ROOT / "harness/data/decomposition-map.yaml")
    map_groups = defaultdict(set)
    for skill, group in skill_to_group.items():
        if group != "hs":  # spine is not a label group
            map_groups[group].add(skill)
    assert map_groups, "expected themed groups in the decomposition map"

    comps = cc.load_components(ROOT / "harness/data/components.yaml")
    for group, skills in map_groups.items():
        comp_skills = set(comps.get(group, {}).get("skills") or [])
        assert comp_skills == skills, (
            "group %r: components.yaml skills %s != decomposition-map membership %s"
            % (group, sorted(comp_skills), sorted(skills)))


# The 7 ck-port groups live ONLY as a documentation comment in the map (the forward
# migrator must not scan them), so the themed cross-check above is blind to them —
# yet skill_selection.group_skills reads them from components.yaml at install time.
# Pin them against the filesystem (the reverse-migrator's own source of truth) and
# pin the whole group SET, so a new/renamed/misclassified group cannot slip in with
# no second-source coverage.
CKPORT_GROUPS = {"ai", "devops", "stack", "uiux", "integrations", "extra", "viz"}
HOOK_COMPONENTS = {"rbac", "decision-capture", "write-deny"}  # hook-bearing, not label groups
_SKILLS_DIR = ROOT / "harness" / "plugins" / "hs" / "skills"


def test_components_group_set_is_fully_classified():
    comps = cc.load_components(ROOT / "harness/data/components.yaml")
    skill_to_group = md.load_map(ROOT / "harness/data/decomposition-map.yaml")
    map_themed = {g for g in skill_to_group.values() if g != "hs"}
    expected = map_themed | CKPORT_GROUPS | HOOK_COMPONENTS
    assert set(comps.keys()) == expected, (
        "components.yaml groups %s != map-themed + ck-port + hook components %s — a "
        "group slipped in with no second-source coverage; update the map or this set"
        % (sorted(comps.keys()), sorted(expected)))


@pytest.mark.dev_repo
def test_ckport_group_members_are_real_skills():
    comps = cc.load_components(ROOT / "harness/data/components.yaml")
    for group in sorted(CKPORT_GROUPS):
        assert group in comps, "ck-port group %r missing from components.yaml" % group
        members = comps[group].get("skills") or []
        assert members, "ck-port group %r lists no skills" % group
        for skill in members:
            assert (_SKILLS_DIR / skill).is_dir(), (
                "ck-port group %r lists %r but harness/plugins/hs/skills/%s/ does "
                "not exist (stale or typo'd member)" % (group, skill, skill))
