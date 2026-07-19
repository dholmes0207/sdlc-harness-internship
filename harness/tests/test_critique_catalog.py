"""test_critique_catalog.py — hs:critique skill + agent + chain wiring.

The critique meta-skill must register like any harness-owned skill (discovered
by catalog.py, hs:-owned, valid compliance-tier), its consolidator agent must
carry a matching frontmatter name, and the declared pipeline must know the
plan -> critique hop. These assertions run against the real shipped files.
"""
import re
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
_SCRIPTS = _HARNESS / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import catalog  # noqa: E402
from catalog import to_dir_id  # noqa: E402

_SKILLS = _HARNESS / "plugins" / "hs" / "skills"
_AGENTS = _HARNESS / "plugins" / "hs" / "agents"
_CHAINS = _HARNESS / "data" / "skill-chains.yaml"


class TestCritiqueSkillRegistration:
    def test_skill_discovered_and_owned(self):
        cat = catalog.load_catalog(_SKILLS)
        assert "critique" in cat["dirs"]
        assert "critique" in cat["owned"]  # location-based: lives under plugins/hs/skills/

    def test_skill_slug_maps_to_dir(self):
        cat = catalog.load_catalog(_SKILLS)
        assert cat["slug_to_dir"].get("critique") == "critique"  # bare name, post name-strip
        assert to_dir_id("hs:critique", cat) == "critique"  # legacy hs:-identity still folds

    def test_compliance_tier_is_valid(self):
        problems = catalog.tier_problems(_SKILLS)
        assert not [p for p in problems if p.startswith("critique:")], problems


class TestConsolidatorAgent:
    def test_agent_file_exists_with_matching_name(self):
        agent = _AGENTS / "critique-consolidator.md"
        assert agent.is_file()
        head = agent.read_text(encoding="utf-8")[:2000]
        m = re.search(r"^name:\s*(.+?)\s*$", head, re.MULTILINE)
        assert m and m.group(1).strip() == "critique-consolidator"


class TestChainWiring:
    def test_plan_to_critique_chain_declared(self):
        import yaml
        raw = yaml.safe_load(_CHAINS.read_text(encoding="utf-8"))
        chains = [list(c) for c in raw.get("chains", [])]
        assert ["hs:plan", "hs:critique"] in chains
