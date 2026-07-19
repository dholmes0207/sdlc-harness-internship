"""hs:gemini skill + registry structure (phase 7).

The skill is a real catalog entry (valid frontmatter, bare `name: gemini` post
the S1 name-prefix-strip standardization), ships OFF (not on the core_immutable
spine, D4), and its registry rows do not drift: any hard hs: route in its
SKILL.md must be a declared dep, and it carries a group label in
components.yaml. The review-output schema is valid JSON with provenance.
"""
import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_SKILL = _ROOT / "plugins" / "hs" / "skills" / "gemini"


def _frontmatter(md_path):
    text = md_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fm = text.split("---\n", 2)[1]
    return yaml.safe_load(fm)


# --- T1: SKILL.md frontmatter is valid, name = gemini (bare, post name-strip) ---
def test_t1_skill_frontmatter_valid():
    fm = _frontmatter(_SKILL / "SKILL.md")
    assert fm["name"] == "hs:gemini"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()


# --- T2: no hard hs: route drifts from skill-deps ---------------------------
def test_t2_handoff_routes_are_declared_deps():
    import sys
    sys.path.insert(0, str(_ROOT / "scripts"))
    import skill_deps
    deps_path = _ROOT / "data" / "skill-deps.yaml"
    data = skill_deps.load_deps(deps_path)
    assert "gemini" in data["skills"], "gemini must be in skill-deps.yaml"
    declared = set(data["skills"]["gemini"].get("deps", []))

    # Parse hard hs: routes from the SKILL body, excluding the advisory
    # 'Related skills' / 'See also' section (soft see-also, exempt).
    import re
    text = (_SKILL / "SKILL.md").read_text(encoding="utf-8")
    lines, skip = [], False
    for ln in text.split("\n"):
        if re.match(r"^#{1,6}\s*(related skills?|see\s*also)\b", ln, re.IGNORECASE):
            skip = True
            continue
        if skip and re.match(r"^#{1,6}\s+\S", ln):
            skip = False
        if not skip:
            lines.append(ln)
    known = set(data["skills"])
    routes = {m.group(1) for m in re.finditer(r"(?:hs|hs-[a-z]+):([a-z][a-z0-9-]+)",
                                              "\n".join(lines))
              if m.group(1) in known and m.group(1) != "gemini"}
    missing = routes - declared
    assert not missing, "hard routes missing from deps: %s" % missing


# --- T3: components.yaml carries a group label for gemini -------------------
def test_t3_components_label_present():
    comp = yaml.safe_load((_ROOT / "data" / "components.yaml").read_text(encoding="utf-8"))
    groups = comp.get("components") or comp.get("groups") or comp
    found = any("gemini" in (g.get("skills") or [])
                for g in groups.values() if isinstance(g, dict))
    assert found, "gemini must appear in some components.yaml group's skills"


# --- T4: gemini ships OFF — never on the immutable spine (D4) ----------------
def test_t4_not_core_immutable():
    import sys
    sys.path.insert(0, str(_ROOT / "scripts"))
    import skill_deps
    data = skill_deps.load_deps(_ROOT / "data" / "skill-deps.yaml")
    assert "gemini" not in data.get("core_immutable", [])


# --- T6: review-output schema is valid JSON with provenance -----------------
def test_t6_review_schema_valid():
    schema = json.loads((_SKILL / "references" / "review-output-schema.json").read_text())
    assert schema["type"] == "object"
    assert "provenance" in schema["properties"]
    prov = schema["properties"]["provenance"]["properties"]
    assert "reviewer_engine" in prov and "reviewer_model" in prov


# --- T7: prompt templates are companion-owned data (survive a stashed skill) -
def test_t7_templates_yaml_companion_owned():
    # NOT under the skill dir — the skill ships OFF and gets stashed; the
    # companion must still find these.
    data = _ROOT / "plugins" / "hs" / "data" / "gemini-prompt-templates.yaml"
    assert data.is_file(), "templates must live in companion-owned data/, not skill references/"
    doc = yaml.safe_load(data.read_text(encoding="utf-8"))
    assert "output_contract" in doc
    purposes = doc["purposes"]
    for p in ("review", "redteam", "research", "delegate"):
        assert p in purposes and purposes[p].get("preamble", "").strip(), \
            "purpose %s must carry a non-empty methodology preamble" % p
    # the review preamble must be methodology, not a one-line persona
    assert len(purposes["review"]["preamble"].splitlines()) >= 8


def test_t7_no_stale_markdown_templates():
    # the markdown draft was replaced by the load-bearing YAML — it must be gone
    assert not (_SKILL / "references" / "prompt-templates.md").exists()
