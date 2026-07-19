"""Drift guard: SKILL.md declared handoffs must be reflected in skill-deps.yaml.

There is no single structured `handoff:` frontmatter field in this codebase.
The most STRUCTURED handoff signal that exists is the explicit namespaced
cross-skill reference inside each SKILL.md: a routing call written as
`hs:<skill>` or `hs-<group>:<skill>` (e.g. "escalate to `hs:bakeoff`",
"runs before `hs:git`"). These are declared routes, not free prose — a bare
word like "test" is ignored; only a namespaced reference counts as an edge.

Two-tier exception: a namespaced reference inside an advisory section
("## Related skills" / "## See also") is a SOFT pointer — a see-also that is
ignored-if-missing, not a co-install dependency. Those sections are stripped
before edge parsing, so a soft pointer is never forced into skill-deps.yaml.
Real routes belong in prose / Workflow / Boundaries and stay enforced.

This guard parses those references and asserts that for every declared edge
src -> dst (dst != src, dst among the 97 skills), dst appears in src's deps
in skill-deps.yaml. The skill-deps graph is seeded from exactly this source,
so the guard is the inverse check that the data file did not drift away from
the SKILL.md routes it was built from.

What is checked (recorded for transparency): every `hs:`/`hs-group:`
namespaced skill reference found inside each of the 96 SKILL.md files. The
97th skill (`common`) is a shared-resource directory with no SKILL.md and is
never a route source; it is still required to have an entry in the graph,
which the loader test covers.
"""

import re
from pathlib import Path

from harness.scripts import skill_deps

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPS_PATH = REPO_ROOT / "harness" / "data" / "skill-deps.yaml"
PLUGINS_DIR = REPO_ROOT / "harness" / "plugins"

# Matches a namespaced skill reference: hs:<name> or hs-<group>:<name>.
_REF = re.compile(r"(?:hs|hs-[a-z]+):([a-z][a-z0-9-]+)")

# Advisory sections — "Related skills" / "See also" — hold SOFT pointers, not
# declared routes. A hs:<skill> there is "ignored if missing": it is a
# see-also, not a co-install dependency, so it must NOT be forced into
# skill-deps.yaml. Real routes live in prose / Workflow / Boundaries and stay
# enforced. This is the two-tier cross-ref split (hard route vs advisory).
_ADVISORY_HEADING = re.compile(r"^(#{1,6})\s*(?:related skills?|see\s*also)\b", re.IGNORECASE)


def _strip_advisory_sections(text: str) -> str:
    """Drop See-also / Related-skills sections before edge parsing.

    A section runs from its advisory heading to the next heading of the same
    or higher level (or end of file). Refs inside are soft and exempt.
    """
    out, skip_at = [], None
    for line in text.split("\n"):
        head = re.match(r"^(#{1,6})\s+\S", line)
        if skip_at is not None:
            if head and len(head.group(1)) <= skip_at:
                skip_at = None  # advisory section ended
            else:
                continue  # still inside the advisory section
        adv = _ADVISORY_HEADING.match(line)
        if adv:
            skip_at = len(adv.group(1))
            continue
        out.append(line)
    return "\n".join(out)


def _known_skills():
    return set(skill_deps.load_deps(DEPS_PATH)["skills"])


def _declared_edges():
    """Return {src: {dst, ...}} of namespaced handoff edges from every SKILL.md."""
    known = _known_skills()
    edges = {}
    skill_files = sorted(PLUGINS_DIR.rglob("SKILL.md"))
    for path in skill_files:
        src = path.parent.name
        if src not in known:
            continue
        text = _strip_advisory_sections(path.read_text(encoding="utf-8"))
        targets = {
            m.group(1)
            for m in _REF.finditer(text)
            if m.group(1) in known and m.group(1) != src
        }
        if targets:
            edges[src] = targets
    return edges


def test_a_structured_handoff_source_actually_exists():
    """Guard against a vacuous always-pass: there must be real edges to check."""
    edges = _declared_edges()
    # The spine alone declares many routes; insist the source is non-trivial.
    assert len(edges) >= 20, "expected a substantial set of declared handoff edges"
    total = sum(len(v) for v in edges.values())
    assert total >= 50, "expected many declared handoff edges across SKILL.md files"


def test_every_declared_handoff_edge_is_a_dep():
    """For each src->dst route declared in SKILL.md, dst must be in src's deps."""
    data = skill_deps.load_deps(DEPS_PATH)
    skills = data["skills"]
    edges = _declared_edges()
    missing = []
    for src, targets in edges.items():
        deps = set(skills[src].get("deps", []))
        for dst in sorted(targets):
            if dst not in deps:
                missing.append(f"{src} -> {dst}")
    assert not missing, "handoff routes missing from skill-deps.yaml deps: " + ", ".join(missing)


def test_advisory_related_sections_are_exempt():
    """A hs:<skill> under a 'Related skills' / 'See also' heading is a soft
    pointer (ignored-if-missing), NOT a declared route: it must be stripped so
    it is never forced into skill-deps.yaml. A ref OUTSIDE such a section is
    still a real route and stays visible to the drift guard."""
    soft = "# Skill\n\n## Related skills\n\n- `hs:deploy`: publish the output.\n"
    assert "deploy" not in {m.group(1) for m in _REF.finditer(_strip_advisory_sections(soft))}

    mixed = "# Skill\n\nRoute to `hs:cook`.\n\n## Related skills\n\n- `hs:deploy`\n"
    seen = {m.group(1) for m in _REF.finditer(_strip_advisory_sections(mixed))}
    assert "cook" in seen and "deploy" not in seen


def test_verified_spine_seeds_are_present():
    """The hand-verified spine handoff seeds must survive in the graph."""
    skills = skill_deps.load_deps(DEPS_PATH)["skills"]
    seeds = {
        "code-review": {"afk", "repomix"},
        "cook": {"bakeoff"},
        "triage": {"bakeoff"},
        "debug": {"brainstorm"},
        "fix": {"brainstorm"},
        "git": {"context-engineering"},
        "scout": {"context-engineering", "repomix", "research"},
        "setup": {"voice"},
        "ship": {"context-engineering", "critique"},
        "understand": {"docs", "context-engineering", "repomix"},
    }
    for src, expected in seeds.items():
        deps = set(skills[src].get("deps", []))
        assert expected <= deps, f"{src} missing seeds {expected - deps}"
