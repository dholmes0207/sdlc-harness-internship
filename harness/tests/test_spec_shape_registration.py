"""Registration invariants for the two PO/BA product-spec skills (spec, shape).

Both skills already ship live SKILL.md files under harness/plugins/hs/skills/, but
land in the install machinery here: skill-deps.yaml (deps == the real SKILL.md
routes), skill-defaults.yaml (default-off + an onboarding cluster), and
components.yaml (an install-time group label). A third invariant pins the
docs-standardize code-default skip for docs/product/ — it must hold even with
an empty .docsignore, since the skip lives in discover.py, not the ignore file.
"""
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "harness" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import skill_deps  # noqa: E402

DEPS_PATH = REPO_ROOT / "harness" / "data" / "skill-deps.yaml"
DEFAULTS_PATH = REPO_ROOT / "harness" / "data" / "skill-defaults.yaml"
COMPONENTS_PATH = REPO_ROOT / "harness" / "data" / "components.yaml"
SKILLS_DIR = REPO_ROOT / "harness" / "plugins" / "hs" / "skills"

EXPECTED_DEPS = {
    "spec": {"critique", "shape"},
    "shape": {"code-review", "cook", "plan", "spec", "test"},
}

# Mirrors test_handoff_deps_drift.py's route-extraction: a namespaced hs:<skill>
# reference outside a Related-skills/See-also advisory section is a real route.
_REF = re.compile(r"(?:hs|hs-[a-z]+):([a-z][a-z0-9-]+)")
_ADVISORY_HEADING = re.compile(r"^(#{1,6})\s*(?:related skills?|see\s*also)\b", re.IGNORECASE)


def _strip_advisory_sections(text: str) -> str:
    out, skip_at = [], None
    for line in text.split("\n"):
        head = re.match(r"^(#{1,6})\s+\S", line)
        if skip_at is not None:
            if head and len(head.group(1)) <= skip_at:
                skip_at = None
            else:
                continue
        adv = _ADVISORY_HEADING.match(line)
        if adv:
            skip_at = len(adv.group(1))
            continue
        out.append(line)
    return "\n".join(out)


def _routes_of(skill: str) -> set:
    text = _strip_advisory_sections((SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8"))
    return {m.group(1) for m in _REF.finditer(text) if m.group(1) != skill}


# --------------------------------------------------------------------------- deps


def test_skill_deps_yaml_has_spec_and_shape_with_exact_deps():
    data = skill_deps.load_deps(DEPS_PATH)
    skills = data["skills"]
    assert "spec" in skills, "skill-deps.yaml missing a 'spec' entry"
    assert "shape" in skills, "skill-deps.yaml missing a 'shape' entry"
    for name, expected in EXPECTED_DEPS.items():
        assert set(skills[name].get("deps", [])) == expected, (
            "%s deps %s != expected %s" % (name, skills[name].get("deps"), sorted(expected))
        )


def test_spec_shape_deps_mirror_their_own_skillmd_routes():
    """Every hs:<skill> route declared in spec/shape's own SKILL.md must be a dep
    (mirrors test_handoff_deps_drift's drift guard, scoped to just these two)."""
    for skill in ("spec", "shape"):
        routes = _routes_of(skill)
        assert routes == EXPECTED_DEPS[skill], (
            "%s SKILL.md routes %s != expected %s" % (skill, sorted(routes), sorted(EXPECTED_DEPS[skill]))
        )


def test_spec_shape_not_in_core_immutable():
    core = set(skill_deps.core_immutable(DEPS_PATH))
    assert "spec" not in core and "shape" not in core, (
        "spec/shape are default-OFF and must never join the always-on spine"
    )


def test_every_dep_of_spec_shape_resolves_to_a_known_skill():
    data = skill_deps.load_deps(DEPS_PATH)
    known = set(data["skills"])
    for name in ("spec", "shape"):
        for dep in data["skills"][name].get("deps", []):
            assert dep in known, "%s -> unknown dep %r" % (name, dep)


# --------------------------------------------------------------------------- defaults


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_default_off_contains_spec_and_shape():
    data = _load_yaml(DEFAULTS_PATH)
    off = set(data.get("default_off") or [])
    assert {"spec", "shape"} <= off, "skill-defaults.yaml default_off must list spec + shape"


def test_clusters_partition_default_off_exactly():
    data = _load_yaml(DEFAULTS_PATH)
    off = set(data.get("default_off") or [])
    clusters = data.get("clusters") or {}
    seen = {}
    for cname, members in clusters.items():
        for m in members or []:
            assert m in off, "cluster %r lists %r not in default_off" % (cname, m)
            assert m not in seen, "%r in two clusters: %r and %r" % (m, seen[m], cname)
            seen[m] = cname
    assert set(seen) == off, "clusters must cover default_off exactly; missing: %r" % sorted(off - set(seen))


def test_spec_shape_land_in_some_cluster():
    data = _load_yaml(DEFAULTS_PATH)
    clusters = data.get("clusters") or {}
    all_members = {m for members in clusters.values() for m in (members or [])}
    assert {"spec", "shape"} <= all_members, "spec/shape must be grouped into an onboarding cluster"


# --------------------------------------------------------------------------- components


def test_components_yaml_has_a_group_covering_spec_and_shape():
    data = _load_yaml(COMPONENTS_PATH)
    comps = data.get("components") or {}
    hit = [name for name, body in comps.items() if {"spec", "shape"} <= set((body or {}).get("skills") or [])]
    assert hit, "no components.yaml group lists both spec and shape under skills:"


# --------------------------------------------------------------------------- docs-standardize code-default skip


_DOCSLIB_DIR = REPO_ROOT / "harness" / "plugins" / "hs" / "skills" / "_docslib"
if str(_DOCSLIB_DIR) not in sys.path:
    sys.path.insert(0, str(_DOCSLIB_DIR))


def test_iter_md_skips_top_level_product_even_with_empty_docsignore(tmp_path):
    from docslib.discover import iter_md

    (tmp_path / "product").mkdir()
    (tmp_path / "product" / "x.md").write_text("# x\n")
    (tmp_path / "guide.md").write_text("# guide\n")

    found = {p.name for p in iter_md(tmp_path, ignore_patterns=[])}
    assert "x.md" not in found, "top-level docs/product/ must be skipped even with an empty .docsignore"
    assert "guide.md" in found, "a normal top-level doc must still be yielded"


def test_iter_md_does_not_over_match_nested_product_dir(tmp_path):
    from docslib.discover import iter_md

    (tmp_path / "foo" / "product").mkdir(parents=True)
    (tmp_path / "foo" / "product" / "y.md").write_text("# y\n")

    found = {str(p.relative_to(tmp_path)) for p in iter_md(tmp_path, ignore_patterns=[])}
    assert "foo/product/y.md" in found, "a nested (non-top-level) product/ dir must NOT be skipped"
