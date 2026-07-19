"""Phase C acceptance: post-collapse topology invariants for the single-hs plugin.

After the reverse migration runs, every non-spine skill lives back under the spine
(`harness/plugins/hs/skills/<skill>`, frontmatter `name: <skill>` — bare, post the
S1 name-prefix-strip standardization; ownership is location-based, see catalog.py),
the themed sibling plugin dirs are gone, and no shipped file may carry a themed-form
reference (`hs-<group>:<skill>` / `hs-<group>/skills/<skill>`) except the
intentionally-kept exempt set the reverse engine honors.

This test reads the canonical map and derives the expected names, so it stays valid
through the migration itself (it never hard-codes a themed literal the migrator would
rewrite). The 97-skill universe completeness is enforced separately by the skill-deps
loader test; here NON_SPINE (the 38 mapped themed skills) is a representative subset.
"""
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import migrate_decomposition as md  # noqa: E402

MAP = md.load_map(ROOT / "harness/data/decomposition-map.yaml")
SPINE = md.spine_skills(MAP)
NON_SPINE = md.non_spine_skills(MAP)
PLUG = ROOT / "harness/plugins"


def _name_line(skill_md: Path) -> str:
    for line in skill_md.read_text().splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return ""


@pytest.mark.dev_repo
def test_non_spine_skills_live_in_spine():
    for skill, group in NON_SPINE.items():
        spine_dir = PLUG / f"hs/skills/{skill}"
        themed_dir = PLUG / f"hs-{group}/skills/{skill}"
        assert spine_dir.is_dir(), f"{skill} not collapsed into hs/skills/"
        assert not themed_dir.exists(), f"{skill} still under themed hs-{group}/"


@pytest.mark.dev_repo
def test_non_spine_frontmatter_carries_hs_name():
    for skill in NON_SPINE:
        md_file = PLUG / f"hs/skills/{skill}/SKILL.md"
        assert md_file.is_file(), f"{skill} SKILL.md missing from hs/"
        assert _name_line(md_file) == f"hs:{skill}", \
            f"{skill} frontmatter name must be hs:{skill} (invocation follows the name)"


def test_no_new_form_refs_remain_in_shipped_tree():
    # reverse --check scans the whole tree (minus the reverse exempt set + keep-marked
    # lines) for any surviving themed-form reference to a collapsed skill.
    assert md.run_migrate(root=ROOT, do_check=True, reverse=True) == 0


def test_no_themed_plugin_dirs_remain():
    # The collapse deletes every hs-<group>/ sibling; only hs/ (+ .claude-plugin/) stays.
    siblings = [p.name for p in PLUG.glob("hs-*") if p.is_dir()]
    assert siblings == [], f"themed plugin dirs still present: {siblings}"


@pytest.mark.dev_repo
def test_every_mapped_skill_resolves_to_spine_dir():
    for skill in MAP:
        d = PLUG / f"hs/skills/{skill}"
        assert d.is_dir(), f"{skill} does not resolve to hs/skills/{skill}"
