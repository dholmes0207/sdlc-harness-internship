"""Tests for harness skill cross-reference and workflow-chain validator.

Covers:
- Valid refs resolve without error
- Broken refs (/hs-x:ghost) are detected
- Orphan skills (no inbound / outbound edges) are detected
- Present chain edges pass; missing ones are reported
- Code-fence refs are excluded
- Self-references are ignored
- Both /hs:<skill> and /hs-<group>:<skill> namespaces match
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure scripts dir is importable
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import validate_skill_crossrefs as vsc  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_skill(root: Path, plugin: str, skill_dir: str, name: str, body: str) -> Path:
    """Create a fake plugin/skill/SKILL.md under root."""
    d = root / plugin / "skills" / skill_dir
    d.mkdir(parents=True, exist_ok=True)
    content = (
        f"---\nname: {name}\ndescription: fake\nuser-invocable: true\n---\n\n"
        + body
    )
    (d / "SKILL.md").write_text(content, encoding="utf-8")
    return d


def _build_tree(tmp_path: Path) -> Path:
    """
    Build a minimal multi-plugin skills tree:

      hs/skills/plan       name: hs:plan       refs -> hs:cook
      hs/skills/cook       name: hs:cook        refs -> hs-think:brainstorm, hs:code-review
      hs-think/skills/brainstorm  name: hs-think:brainstorm  (no outbound refs)
      hs/skills/orphan     name: hs:orphan      (no refs in or out)
      hs/skills/broken     name: hs:broken      refs -> /hs-x:ghost (nonexistent)
    """
    _make_skill(tmp_path, "hs", "plan", "hs:plan",
                "After deciding, use /hs:cook to implement.\n")
    _make_skill(tmp_path, "hs", "cook", "hs:cook",
                "Call /hs-think:brainstorm for ideas, then /hs:code-review.\n")
    _make_skill(tmp_path, "hs-think", "brainstorm", "hs-think:brainstorm",
                "Pure ideation, no further refs.\n")
    _make_skill(tmp_path, "hs", "orphan", "hs:orphan",
                "This skill is standalone.\n")
    _make_skill(tmp_path, "hs", "broken", "hs:broken",
                "See /hs-x:ghost for help.\n")
    return tmp_path


# ── tests ────────────────────────────────────────────────────────────────────

class TestScanAllSkills:
    def test_collects_all_plugins(self, tmp_path):
        _build_tree(tmp_path)
        skills = vsc.scan_all_skills(tmp_path)
        names = {d["name"] for d in skills.values()}
        assert "hs:plan" in names
        assert "hs:cook" in names
        assert "hs-think:brainstorm" in names

    def test_spine_ref_detected(self, tmp_path):
        """plan body contains /hs:cook — must appear in body_refs."""
        _build_tree(tmp_path)
        skills = vsc.scan_all_skills(tmp_path)
        plan_entry = next(d for d in skills.values() if d["name"] == "hs:plan")
        assert "hs:cook" in plan_entry["body_refs"]

    def test_namespaced_ref_detected(self, tmp_path):
        """/hs-think:brainstorm in cook body must be captured."""
        _build_tree(tmp_path)
        skills = vsc.scan_all_skills(tmp_path)
        cook_entry = next(d for d in skills.values() if d["name"] == "hs:cook")
        assert "hs-think:brainstorm" in cook_entry["body_refs"]

    def test_code_fence_refs_excluded(self, tmp_path):
        """Refs inside ``` fences must not appear in body_refs."""
        _make_skill(tmp_path, "hs", "fenced", "hs:fenced",
                    "Normal text.\n\n```\n/hs:cook should not match\n```\n")
        skills = vsc.scan_all_skills(tmp_path)
        fenced = next(d for d in skills.values() if d["name"] == "hs:fenced")
        assert "hs:cook" not in fenced["body_refs"]

    def test_bare_ref_without_slash_detected(self, tmp_path):
        """A prose handoff written bare (hs:cook, no leading slash — the harness
        writes routes in prose/backticks without the slash-command slash) must
        count, so the workflow-chain audit sees the real handoffs."""
        _make_skill(tmp_path, "hs", "scout", "hs:scout",
                    "Scout output is input for `hs:cook` and `hs:debug`.\n")
        _make_skill(tmp_path, "hs", "cook", "hs:cook", "Cook stuff.\n")
        skills = vsc.scan_all_skills(tmp_path)
        scout = next(d for d in skills.values() if d["name"] == "hs:scout")
        assert "hs:cook" in scout["body_refs"]
        assert "hs:debug" in scout["body_refs"]

    def test_self_reference_ignored(self, tmp_path):
        """A skill that references itself must not produce a self-edge."""
        _make_skill(tmp_path, "hs", "self-ref", "hs:self-ref",
                    "Calls /hs:self-ref recursively.\n")
        skills = vsc.scan_all_skills(tmp_path)
        sr = next(d for d in skills.values() if d["name"] == "hs:self-ref")
        assert "hs:self-ref" not in sr["body_refs"]

    def test_skip_dirs_respected(self, tmp_path):
        """Skills inside SKIP_DIRS are not included."""
        skip_dir = tmp_path / "hs" / "skills" / "_shared"
        skip_dir.mkdir(parents=True, exist_ok=True)
        (skip_dir / "SKILL.md").write_text(
            "---\nname: hs:should-skip\ndescription: x\nuser-invocable: true\n---\nHello.\n",
            encoding="utf-8",
        )
        skills = vsc.scan_all_skills(tmp_path)
        names = {d["name"] for d in skills.values()}
        assert "hs:should-skip" not in names


class TestBuildReferenceGraph:
    def test_valid_edge_present(self, tmp_path):
        """plan -> cook edge must appear in graph edges."""
        _build_tree(tmp_path)
        skills = vsc.scan_all_skills(tmp_path)
        graph = vsc.build_reference_graph(skills)
        # At least one skill points to hs:cook
        all_targets = {t for targets in graph["edges"].values() for t in targets}
        assert "hs:cook" in all_targets

    def test_broken_ref_detected(self, tmp_path):
        """/hs-x:ghost is not a known skill — must appear in broken list."""
        _build_tree(tmp_path)
        skills = vsc.scan_all_skills(tmp_path)
        graph = vsc.build_reference_graph(skills)
        broken_refs = [ref for _, ref in graph["broken"]]
        assert "hs-x:ghost" in broken_refs

    def test_orphan_detected(self, tmp_path):
        """hs:orphan has no inbound or outbound edges — must be in orphans."""
        _build_tree(tmp_path)
        skills = vsc.scan_all_skills(tmp_path)
        graph = vsc.build_reference_graph(skills)
        # orphan skill name is in orphans list
        assert "hs:orphan" in graph["orphans"]

    def test_no_false_broken_for_valid_ref(self, tmp_path):
        """hs:cook is a real skill — must not appear as broken ref."""
        _build_tree(tmp_path)
        skills = vsc.scan_all_skills(tmp_path)
        graph = vsc.build_reference_graph(skills)
        broken_refs = [ref for _, ref in graph["broken"]]
        assert "hs:cook" not in broken_refs


class TestRuleReferences:
    """CS-10 (K4, phase-3): rule-layer referential integrity — broken +
    orphan checks for `harness/rules/*.md`, mirroring the skill/agent
    broken+orphan checks above. "Routed" (not orphan) = cited in a SKILL.md
    body OR named bare in CLAUDE.md (K10, red-team B1 PROVEN)."""

    def _mk_rule(self, root: Path, name: str) -> Path:
        d = root / "harness" / "rules"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{name}.md"
        p.write_text(f"# {name}\n\nA rule.\n", encoding="utf-8")
        return p

    def test_v1_broken_rule_ref_detected(self, tmp_path):
        """A SKILL.md citing harness/rules/ghost.md (no such file) must
        appear in broken_rules."""
        self._mk_rule(tmp_path, "real-rule")
        _make_skill(tmp_path, "hs", "citer", "hs:citer",
                    "See harness/rules/ghost.md for the policy.\n")
        skills = vsc.scan_all_skills(tmp_path)
        known_rules = vsc.collect_rule_names(tmp_path)
        result = vsc.check_rule_references(skills, known_rules)
        broken_refs = [ref for _, ref in result["broken_rules"]]
        assert "harness/rules/ghost.md" in broken_refs

    def test_v2_orphan_rule_detected(self, tmp_path):
        """A rule file nobody cites (SKILL.md or CLAUDE.md) is an orphan."""
        self._mk_rule(tmp_path, "lonely-rule")
        _make_skill(tmp_path, "hs", "citer", "hs:citer", "No rule cited here.\n")
        skills = vsc.scan_all_skills(tmp_path)
        known_rules = vsc.collect_rule_names(tmp_path)
        result = vsc.check_rule_references(skills, known_rules)
        assert "harness/rules/lonely-rule.md" in result["orphan_rules"]

    def test_v3_cited_rule_not_orphan(self, tmp_path):
        """A rule cited in a SKILL.md body must NOT appear as orphan."""
        self._mk_rule(tmp_path, "used-rule")
        _make_skill(tmp_path, "hs", "citer", "hs:citer",
                    "See harness/rules/used-rule.md for detail.\n")
        skills = vsc.scan_all_skills(tmp_path)
        known_rules = vsc.collect_rule_names(tmp_path)
        result = vsc.check_rule_references(skills, known_rules)
        assert "harness/rules/used-rule.md" not in result["orphan_rules"]

    def test_v4_regex_precision_rejects_lookalike_paths(self):
        """harness/rulesX/y.md and docs/rules/z.md must not match RULE_REF_RE
        — a lookalike path is not a rule citation."""
        assert vsc.RULE_REF_RE.findall("harness/rulesX/y.md") == []
        assert vsc.RULE_REF_RE.findall("docs/rules/z.md") == []

    def test_v4b_claude_md_only_citation_not_orphan(self, tmp_path):
        """A rule named bare in CLAUDE.md (K10) with zero SKILL.md-body
        citations must NOT be flagged orphan."""
        self._mk_rule(tmp_path, "claude-only-rule")
        _make_skill(tmp_path, "hs", "citer", "hs:citer", "Nothing rule-related.\n")
        (tmp_path / "CLAUDE.md").write_text(
            "Routing index: `claude-only-rule` (always-load).\n", encoding="utf-8")
        skills = vsc.scan_all_skills(tmp_path)
        known_rules = vsc.collect_rule_names(tmp_path)
        claude_routed = vsc.collect_claude_md_rule_refs(tmp_path)
        result = vsc.check_rule_references(skills, known_rules, claude_routed)
        assert "harness/rules/claude-only-rule.md" not in result["orphan_rules"]

    def test_v4c_real_tree_always_load_rules_not_orphan(self):
        """Real-tree run: the 3 always-load rules (K10, red-team B1 PROVEN)
        route via CLAUDE.md bare-name mentions, zero SKILL.md-body citations —
        must never surface as orphan on the actual repo."""
        repo_root = Path(__file__).resolve().parents[2]
        plugins_root = repo_root / "harness" / "plugins"
        skills = vsc.scan_all_skills(plugins_root)
        known_rules = vsc.collect_rule_names(repo_root)
        claude_routed = vsc.collect_claude_md_rule_refs(repo_root)
        result = vsc.check_rule_references(skills, known_rules, claude_routed)
        for stem in ("disabled-group-handling", "primary-workflow", "skill-routing"):
            ref = f"harness/rules/{stem}.md"
            assert ref not in result["orphan_rules"], (
                f"{ref} flagged orphan on the real tree — K10 exception broke")

    def test_v4c_real_tree_no_e1_rule_orphan(self):
        """No E1 rule (net-new this epic, including scoring-rigor-contract
        from this phase) may be flagged orphan on the real tree."""
        repo_root = Path(__file__).resolve().parents[2]
        plugins_root = repo_root / "harness" / "plugins"
        skills = vsc.scan_all_skills(plugins_root)
        known_rules = vsc.collect_rule_names(repo_root)
        claude_routed = vsc.collect_claude_md_rule_refs(repo_root)
        result = vsc.check_rule_references(skills, known_rules, claude_routed)
        e1_rules = [
            "port-layering-and-capability-assignment", "architectural-constraints",
            "intake-and-interview-discipline", "plain-language-phrasing",
            "testability-triad", "scope-and-contract-discipline",
            "plan-quality-goodhart-premortem", "scoring-rigor-contract",
        ]
        for stem in e1_rules:
            ref = f"harness/rules/{stem}.md"
            assert ref not in result["orphan_rules"], f"{ref} flagged orphan"

    def test_v4d_fenced_route_line_not_counted(self, tmp_path):
        """A route-line inside a code fence must not satisfy a citation — the
        shared fence-strip extractor (K11) hides it from CS-10 too."""
        self._mk_rule(tmp_path, "fenced-rule")
        _make_skill(tmp_path, "hs", "citer", "hs:citer",
                    "```text\nharness/rules/fenced-rule.md\n```\n")
        skills = vsc.scan_all_skills(tmp_path)
        known_rules = vsc.collect_rule_names(tmp_path)
        result = vsc.check_rule_references(skills, known_rules)
        assert "harness/rules/fenced-rule.md" in result["orphan_rules"]

    def test_extractor_parity_with_rule_route_existence(self):
        """K11 (red-team M2, PROVEN): F3's extractor (`_rule_ref_util.extract_rule_refs`)
        and CS-10's (`validate_skill_crossrefs.extract_rule_refs`) must be the
        SAME function — never two that could drift on a fenced-vs-prose fixture."""
        from _rule_ref_util import extract_rule_refs as f3_extract

        assert f3_extract is vsc.extract_rule_refs, (
            "F3 and CS-10 import different extractor objects — must be one shared "
            "function (harness/tests/_rule_ref_util.py re-exports the canonical "
            "validate_skill_crossrefs.extract_rule_refs)"
        )

        fenced = (
            "```text\nharness/rules/scope-and-contract-discipline.md\n```\n"
        )
        prose = "See harness/rules/scope-and-contract-discipline.md for detail.\n"
        assert f3_extract(fenced) == vsc.extract_rule_refs(fenced) == set()
        assert (
            f3_extract(prose)
            == vsc.extract_rule_refs(prose)
            == {"harness/rules/scope-and-contract-discipline.md"}
        )


class TestCheckExpectedWorkflows:
    def test_missing_chain_edge_reported(self, tmp_path):
        """A chain pair not connected by any edge must be reported as missing."""
        # Only build plan and cook — no code-review or ship
        _make_skill(tmp_path, "hs", "plan", "hs:plan",
                    "Use /hs:cook next.\n")
        _make_skill(tmp_path, "hs", "cook", "hs:cook",
                    "Implementation only.\n")  # no ref to code-review
        skills = vsc.scan_all_skills(tmp_path)
        graph = vsc.build_reference_graph(skills)
        missing = vsc.check_expected_workflows(graph)
        chain_pairs = [(m["from"], m["to"]) for m in missing]
        # development chain: cook -> code-review is missing
        assert ("hs:cook", "hs:code-review") in chain_pairs

    def test_present_chain_edge_not_reported(self, tmp_path):
        """plan -> cook edge is present — must NOT appear in missing."""
        _make_skill(tmp_path, "hs", "plan", "hs:plan",
                    "Use /hs:cook to implement.\n")
        _make_skill(tmp_path, "hs", "cook", "hs:cook",
                    "Implementation done.\n")
        skills = vsc.scan_all_skills(tmp_path)
        graph = vsc.build_reference_graph(skills)
        missing = vsc.check_expected_workflows(graph)
        chain_pairs = [(m["from"], m["to"]) for m in missing]
        assert ("hs:plan", "hs:cook") not in chain_pairs

    def test_investigation_chain_missing_brainstorm(self, tmp_path):
        """scout + debug without brainstorm must flag scout:debug->debug:brainstorm gap."""
        _make_skill(tmp_path, "hs", "scout", "hs:scout",
                    "Explore with /hs:debug.\n")
        _make_skill(tmp_path, "hs", "debug", "hs:debug",
                    "Diagnose issue.\n")  # no ref to brainstorm
        skills = vsc.scan_all_skills(tmp_path)
        graph = vsc.build_reference_graph(skills)
        missing = vsc.check_expected_workflows(graph)
        chain_pairs = [(m["from"], m["to"]) for m in missing]
        # Post-collapse: brainstorm lives under the single hs plugin, so the
        # investigation chain terminal edge is hs:debug -> hs:brainstorm.
        assert ("hs:debug", "hs:brainstorm") in chain_pairs
