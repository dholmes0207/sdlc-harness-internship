"""Loader + resolver behaviour for the skill dependency graph.

The installer reads this graph to auto-tick a picked skill's transitive deps.
These tests pin the loader's coverage guarantees, validation, and the
transitive-closure resolver (which must terminate on cycles).
"""

from pathlib import Path

import pytest

from harness.scripts import skill_deps

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPS_PATH = REPO_ROOT / "harness" / "data" / "skill-deps.yaml"

# The full set of skills/libs, by group, mirroring decomposition-map.yaml.
# Includes shared resource dirs (common, _docslib) that have no SKILL.md but
# must appear in skill-deps.yaml (108 skills as of 2026-06-30).
ALL_SKILLS = {
    # spine (13)
    "plan", "cook", "test", "ship", "fix", "debug", "code-review",
    "review-pr", "git", "scout", "understand", "setup", "triage",
    # flow (12)
    "vibe", "loop", "afk", "autonomous-bell", "goal", "team", "worktree", "compound",
    "project-management", "plans-kanban", "workflow-orchestrate",
    "coding-agent-orchestration",
    # think (7)
    "brainstorm", "predict", "scenario", "sequential-thinking",
    "problem-solving", "critique", "bakeoff",
    # research (9)
    "research", "discover", "repomix", "techstack",
    "security-scan", "manual-test", "contract-test", "eval-bootstrap",
    "deep-swe",
    # create (6)
    "skill-creator", "harness-creator", "mcp-builder", "agentize", "port",
    "bootstrap",
    # mem (8)
    "remember", "insights", "journal", "retro", "docs", "docs-seeker",
    "document-skills", "interview-docs",
    # meta (7)
    "find-skills", "use", "voice", "context-engineering", "project-organization",
    "rule-author", "folder-context",
    # ai (11)
    "ai-artist", "ai-multimodal", "common", "gemini", "partner", "html-video",
    "media-processing", "remotion", "shader", "stitch", "threejs",
    # devops (7)
    "agent-browser", "chrome-profile", "cleanup", "deploy", "devops", "release",
    "web-testing",
    # stack (8)
    "backend-development", "better-auth", "databases", "frontend-development",
    "mobile-development", "react-best-practices", "tanstack", "web-frameworks",
    # uiux (6)
    "design", "frontend-design", "show-off", "ui-styling", "ui-ux",
    "web-design-guidelines",
    # integrations (5)
    "gkg", "google-adk-python", "payment-integration", "shopify", "use-mcp",
    # extra (10)
    "ask", "copywriting", "cti-expert", "ghpm", "llms",
    "markdown-novel-viewer", "mintlify", "prompt", "watzup", "handoff",
    # viz (6)
    "excalidraw", "graphify", "mermaidjs", "preview", "tech-graph", "drawio",
    # docs-ssot pipeline (3 skills + 1 shared lib)
    "docs-scaffold", "docs-standardize", "docs-build",
    "_docslib",  # shared lib, no SKILL.md; like common in the ai group
    # product (2): PO/BA product-spec pair
    "spec", "shape",
    # AK-adapt (3): advisory + issue intake + ported reasoning protocol
    "advise", "issue-to-plan", "fable-thinking",
}


def test_every_skill_has_a_graph_entry():
    """Every one of the skills/libs must appear as a key in the graph."""
    data = skill_deps.load_deps(DEPS_PATH)
    assert len(ALL_SKILLS) == 124
    assert set(data["skills"]) == ALL_SKILLS


def test_core_immutable_is_sixteen_and_contains_proxy_floor():
    """The always-on floor is the 13 spine + the off-skill proxy trio (use,
    find-skills, cleanup) — the skills that must stay reachable to run, route to, and
    sweep disabled skills (interview #22)."""
    core = set(skill_deps.core_immutable(DEPS_PATH))
    assert len(core) == 16
    assert {"use", "find-skills", "cleanup"} <= core


def test_every_dep_references_a_known_skill():
    """No dep may dangle to a name outside the known skill universe."""
    data = skill_deps.load_deps(DEPS_PATH)
    for skill, body in data["skills"].items():
        for dep in body.get("deps", []):
            assert dep in ALL_SKILLS, f"{skill} -> unknown dep {dep!r}"


def test_load_raises_naming_the_unknown_dep(tmp_path):
    """An unknown dep must raise ValueError that names the offender."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "core_immutable: [plan]\n"
        "skills:\n"
        "  plan: { deps: [] }\n"
        "  cook: { deps: [does-not-exist] }\n"
    )
    with pytest.raises(ValueError) as excinfo:
        skill_deps.load_deps(bad)
    assert "does-not-exist" in str(excinfo.value)


def test_load_raises_when_a_dep_target_has_no_entry(tmp_path):
    """A dep that points at a name with no top-level entry is unknown."""
    bad = tmp_path / "missing.yaml"
    # 'test' is referenced as a dep but never declared as a skill key.
    bad.write_text(
        "core_immutable: [plan]\n"
        "skills:\n"
        "  plan: { deps: [] }\n"
        "  cook: { deps: [test] }\n"
    )
    with pytest.raises(ValueError) as excinfo:
        skill_deps.load_deps(bad)
    assert "test" in str(excinfo.value)


def test_resolve_of_a_leaf_returns_just_itself():
    """A skill with no deps resolves to a set containing only itself."""
    # 'databases' is a genuine leaf in the stack group.
    assert skill_deps.resolve({"databases"}, DEPS_PATH) == {"databases"}


def test_resolve_includes_declared_deps_and_is_closed():
    """resolve(cook) is a superset of cook's deps and closed under deps."""
    data = skill_deps.load_deps(DEPS_PATH)
    resolved = skill_deps.resolve({"cook"}, DEPS_PATH)
    assert "cook" in resolved
    # Every declared dep of cook is auto-ticked.
    for dep in data["skills"]["cook"]["deps"]:
        assert dep in resolved
    # Closure: every resolved skill's own deps are also present.
    for skill in resolved:
        for dep in data["skills"][skill]["deps"]:
            assert dep in resolved, f"{skill} -> {dep} not closed"


def test_resolve_terminates_on_cycles():
    """The graph contains cycles (e.g. plan<->cook); resolve must terminate."""
    # plan depends on cook, cook depends on plan -> a 2-cycle.
    resolved = skill_deps.resolve({"plan"}, DEPS_PATH)
    assert {"plan", "cook"} <= resolved


def test_resolve_of_multiple_seeds_unions_their_closures():
    """resolve of a set seeds equals the union of each seed's closure."""
    both = skill_deps.resolve({"databases", "shopify"}, DEPS_PATH)
    assert {"databases", "shopify"} <= both
    one = skill_deps.resolve({"databases"}, DEPS_PATH)
    other = skill_deps.resolve({"shopify"}, DEPS_PATH)
    assert both == one | other
