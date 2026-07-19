"""hs:partner ships OFF (phase 6, twin of test_gemini_skill_structure.py T3/T4).

A fresh install omits partner by default: components.yaml carries it under
the `ai` group (an omit-at-install group label, not core_immutable), and
skill-deps.yaml declares it a true leaf so the dep-graph resolver knows it.
"""
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent


def test_partner_in_ai_group_and_skill_deps():
    comp = yaml.safe_load((_ROOT / "data" / "components.yaml").read_text(encoding="utf-8"))
    groups = comp.get("components") or comp.get("groups") or comp
    found = any("partner" in (g.get("skills") or [])
                for g in groups.values() if isinstance(g, dict))
    assert found, "partner must appear in some components.yaml group's skills"

    import sys
    sys.path.insert(0, str(_ROOT / "scripts"))
    import skill_deps
    data = skill_deps.load_deps(_ROOT / "data" / "skill-deps.yaml")
    assert "partner" in data["skills"], "partner must be in skill-deps.yaml"
    assert data["skills"]["partner"].get("deps", []) == []
    assert "partner" not in data.get("core_immutable", []), \
        "partner ships OFF — never on the immutable spine"
