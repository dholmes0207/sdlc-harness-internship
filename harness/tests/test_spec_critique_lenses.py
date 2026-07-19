"""test_spec_critique_lenses.py — spec-artifact critique routing into hs:critique.

hs:critique's classifier only recognizes plan/decision/design/code/diff
(critique/SKILL.md:32; unknown -> default) — it will NEVER auto-pick a spec key. A
spec artifact is routed EXPLICITLY: `hs:critique <path> --lenses <spec set>`. This
suite is a ROUTING test: it drives `spec_critique_scan.lens_set_for()`, the resolver
that route reads, not a bare "the key exists in critique.yaml" check. It also pins
the pre-existing plan/decision/design/code/diff/default lens sets byte-for-byte, so
adding the spec lens keys is provably additive-only (no regression to the
classifier-driven route).
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_AGENTS = ROOT / "harness" / "plugins" / "hs" / "agents"
_CRITIQUE = ROOT / "harness" / "data" / "critique.yaml"
# Literal path keeps the stashed-skill collect_ignore coupling working:
# harness/plugins/hs/skills/spec/scripts
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402
from conftest import VALID, make_proj  # noqa: E402

_mods = load_skill_scripts(
    _SPEC_SCRIPTS,
    ["id_grammar", "spec_graph", "frontmatter_parser", "encoding_utils",
     "check_traceability", "spec_critique_scan"],
)
# spec_critique_scan's structural-findings helper does a LAZY (function-body)
# `import check_traceability` that resolves at CALL time, after
# load_skill_scripts has already restored sys.path — pin it into sys.modules
# for this test module's lifetime (mirrors test_spec_visualize.py's identical
# lazy-import gotcha for the same module).
sys.modules["check_traceability"] = _mods["check_traceability"]
spec_critique_scan = _mods["spec_critique_scan"]
spec_graph = _mods["spec_graph"]
check_traceability = _mods["check_traceability"]

# The SSOT spec lens-set (critique.yaml spec-family keys) that lens_set_for()
# must resolve for every spec-family artifact path.
SPEC_LENS_SET = ["spec-tech-critic", "spec-craft-critic", "product-value-critic", "market-fit-critic"]
SPEC_FAMILY_KEYS = ("spec", "vision", "brd", "prd", "epic", "story")

# The lens sets for plan/decision/design/code/diff/default EXACTLY as they existed
# before the spec lens keys were added (harness/data/critique.yaml, read at
# plan-authoring time). Adding spec keys may only ADD new keys — these must not
# move a single entry.
_PRE_EXISTING_LENSES = {
    "plan": ["red-teamer", "independent-revalidator", "brainstormer",
             "product-value-critic", "market-fit-critic"],
    "decision": ["red-teamer", "independent-revalidator", "brainstormer",
                 "product-value-critic", "market-fit-critic"],
    "design": ["red-teamer", "independent-revalidator", "brainstormer",
               "product-value-critic", "market-fit-critic"],
    "code": ["red-teamer", "code-reviewer", "independent-revalidator"],
    "diff": ["red-teamer", "code-reviewer", "independent-revalidator"],
    "default": ["red-teamer", "independent-revalidator", "brainstormer"],
}


def _critique_lenses():
    raw = yaml.safe_load(_CRITIQUE.read_text(encoding="utf-8"))
    return raw.get("lenses", {})


def _agent_name(slug):
    f = _AGENTS / ("%s.md" % slug)
    if not f.is_file():
        return None
    head = f.read_text(encoding="utf-8")[:2000]
    m = re.search(r"^name:\s*(.+?)\s*$", head, re.MULTILINE)
    return m.group(1).strip() if m else None


class TestRoutingResolvesSpecLensSet:
    """The routing test: `lens_set_for()` on a REAL spec artifact path resolves
    the spec lens set — not a config-presence assertion."""

    def test_story_artifact_resolves_spec_lens_set(self):
        story = VALID / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
        assert spec_critique_scan.lens_set_for(story) == SPEC_LENS_SET

    def test_epic_and_prd_artifacts_resolve_spec_lens_set(self):
        epic = VALID / "docs" / "product" / "epics" / "PRD-AUTH-E1.md"
        prd = VALID / "docs" / "product" / "prds" / "auth.md"
        assert spec_critique_scan.lens_set_for(epic) == SPEC_LENS_SET
        assert spec_critique_scan.lens_set_for(prd) == SPEC_LENS_SET

    def test_vision_and_brd_artifacts_resolve_spec_lens_set(self):
        vision = VALID / "docs" / "product" / "vision.md"
        brd = VALID / "docs" / "product" / "brd.md"
        assert spec_critique_scan.lens_set_for(vision) == SPEC_LENS_SET
        assert spec_critique_scan.lens_set_for(brd) == SPEC_LENS_SET


class TestRoutingLeavesOtherArtifactTypesAlone:
    """The classifier still owns plan/code/etc.; the resolver must not hijack them."""

    def test_plan_artifact_does_not_resolve_spec_lenses(self):
        # The resolver classifies purely by PATH SHAPE (stem/parent/parts) —
        # it never opens the file — so a plan path that does not exist on
        # disk is a valid, neutral fixture here.
        plan_path = ROOT / "plans" / "some-active-plan" / "plan.md"
        assert spec_critique_scan.lens_set_for(plan_path) is None

    def test_code_artifact_does_not_resolve_spec_lenses(self):
        code_path = ROOT / "harness" / "scripts" / "catalog.py"
        assert spec_critique_scan.lens_set_for(code_path) is None


class TestSpecLensAgentsResolve:
    """Every lens named in the spec set must resolve to a real agent file whose
    frontmatter `name:` matches the slug (mirrors test_critique_lens_agents.py)."""

    def test_every_spec_lens_resolves_to_an_agent(self):
        missing = [n for n in SPEC_LENS_SET if _agent_name(n) != n]
        assert not missing, "spec lens names without a matching agent file: %s" % missing

    def test_new_agents_are_read_only_by_tool(self):
        for slug in ("spec-craft-critic", "spec-tech-critic"):
            head = (_AGENTS / ("%s.md" % slug)).read_text(encoding="utf-8")[:2000]
            m = re.search(r"^tools:\s*(.+?)\s*$", head, re.MULTILINE)
            assert m, "agent %s missing a tools: frontmatter line" % slug
            tools = {t.strip() for t in m.group(1).split(",")}
            forbidden = tools & {"Write", "Edit", "NotebookEdit", "Task"}
            assert not forbidden, "%s carries write/spawn tool(s): %s" % (slug, forbidden)


class TestRegressionGuardExistingLensSetsUnchanged:
    """Byte-for-byte guard: adding spec lens keys may ONLY add keys to critique.yaml."""

    def test_pre_existing_lens_sets_are_unchanged(self):
        lenses = _critique_lenses()
        for key, expected in _PRE_EXISTING_LENSES.items():
            assert lenses.get(key) == expected, (
                "lens set for %r drifted from its pre-existing value: got %r, want %r"
                % (key, lenses.get(key), expected))


class TestScanBundleCitesRealSourceLines:
    """spec_critique_scan builds citation ground-truth: source_files keyed by ID,
    line-numbered against the REAL artifact text (never a guessed line)."""

    def test_source_files_keyed_by_id_line_numbered(self):
        bundle = spec_critique_scan.build_scan(VALID, "PRD-AUTH-E1-S1")
        assert "PRD-AUTH-E1-S1" in bundle["source_files"]
        lines = bundle["source_files"]["PRD-AUTH-E1-S1"]
        assert lines, "source_files entry must not be empty"
        assert lines[0].startswith("1: ")
        assert any("id: PRD-AUTH-E1-S1" in line for line in lines), (
            "citation ground truth must contain the real frontmatter line, not a guess")

    def test_bundle_never_invents_a_line_for_a_missing_target(self):
        bundle = spec_critique_scan.build_scan(VALID, "PRD-DOES-NOT-EXIST")
        assert bundle["source_files"].get("PRD-DOES-NOT-EXIST") in (None, [])


class TestStructuralFindingsWired:
    """`structural_findings` must carry the real check_traceability output —
    not silently always []. A stale-attribute lookup (`getattr(module, "run")`
    against a module that only exposes `check(graph)`) made this permanently
    empty regardless of what the target spec actually looked like."""

    def test_orphan_story_surfaces_in_structural_findings(self, tmp_path):
        proj = make_proj(tmp_path)
        story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
        story.write_text(
            story.read_text(encoding="utf-8").replace("epic: PRD-AUTH-E1\n", ""),
            encoding="utf-8",
        )
        bundle = spec_critique_scan.build_scan(proj, "PRD-AUTH-E1-S1")
        checks = {f["check"] for f in bundle["structural_findings"]}
        assert "orphan_story" in checks

    def test_structural_findings_match_check_traceability_directly(self):
        # The bundle's structural_findings must be the REAL check_traceability
        # output for VALID, not an always-empty stand-in — VALID is known to
        # carry exactly one finding (the tree-view footer asserts "1 findings"
        # elsewhere in this suite).
        graph = spec_graph.build_graph(VALID)
        expected = check_traceability.check(graph)
        bundle = spec_critique_scan.build_scan(VALID, "PRD-AUTH-E1-S1")
        assert bundle["structural_findings"] == expected
        assert len(expected) == 1


class TestLensSetForFailsSoftOnMalformedCritiqueYaml:
    """`_load_spec_lenses` must fail-soft on ANY malformed critique.yaml, not
    just a missing file. PyYAML raises well past `yaml.YAMLError`: an
    explicit-tag scalar (e.g. `!!timestamp 'not a ts'`) raises a bare
    AttributeError, and an out-of-range date raises a bare ValueError —
    either would crash the lens resolver under a narrower catch."""

    def test_bad_timestamp_tag_degrades_to_empty_lens_set(self, tmp_path):
        bad = tmp_path / "critique.yaml"
        bad.write_text("lenses:\n  spec: [x]\nbad: !!timestamp 'not a ts'\n", encoding="utf-8")
        story = VALID / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
        assert spec_critique_scan.lens_set_for(story, critique_path=bad) is None

    def test_out_of_range_date_degrades_to_empty_lens_set(self, tmp_path):
        bad = tmp_path / "critique.yaml"
        bad.write_text("lenses:\n  spec: [x]\nbad: 2026-13-99\n", encoding="utf-8")
        story = VALID / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
        assert spec_critique_scan.lens_set_for(story, critique_path=bad) is None


class TestNoSecondCritiqueEngine:
    """Guard: spec_critique_scan feeds hs:critique's engine, it does not become a
    second consolidator/humanizer."""

    def test_no_consolidator_or_humanizer_script_in_spec_scripts(self):
        names = {p.name for p in _SPEC_SCRIPTS.glob("*.py")}
        offenders = [n for n in names if "consolidat" in n or "humaniz" in n]
        assert not offenders, "a second critique-engine script snuck in: %s" % offenders

    def test_scan_module_defines_no_consolidate_or_humanize_function(self):
        public = [n for n in dir(spec_critique_scan) if not n.startswith("_")]
        offenders = [n for n in public if "consolidat" in n.lower() or "humaniz" in n.lower()]
        assert not offenders, "spec_critique_scan must not consolidate/humanize itself: %s" % offenders
