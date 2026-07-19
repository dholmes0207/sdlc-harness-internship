"""Presence checks for the prose-only learnings adopted from the Aperant comparison.

These improvements live entirely in skill/reference markdown (no Python to exercise):
git-environment isolation rules, the complexity-hint cross-check in hs:plan, and the
contract-delta obligation in hs:code-review. The "test" is a grep-for-presence that
plays the red->green role for a prose artifact. Marked ``dev_repo`` so it tracks the
shipped skill tree on the development repo and auto-skips on installed copies where the
manifest may omit a skill.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_HS = ROOT / "harness" / "plugins" / "hs" / "skills"

pytestmark = pytest.mark.dev_repo


def _read(rel: str) -> str:
    return (_HS / rel).read_text(encoding="utf-8")


# --- #8-slim: git-environment isolation -------------------------------------

def test_isolation_rules_lists_git_env_vars():
    txt = _read("worktree/references/isolation-rules.md")
    assert "## Git environment isolation" in txt
    for var in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
    ):
        assert var in txt, f"missing {var}"


def test_isolation_rules_exact_match_branch_before_destructive():
    txt = _read("worktree/references/isolation-rules.md").lower()
    assert "exact-match" in txt or "exact match" in txt
    assert "rev-parse" in txt and "abbrev-ref" in txt


# --- #1: complexity hint in hs:plan Understand ------------------------------

def test_plan_skill_has_complexity_hint():
    txt = _read("plan/SKILL.md")
    assert "complexity:" in txt
    assert "cross-check" in txt
    # both branches: interactive AskUserQuestion + headless Validation Log
    assert "AskUserQuestion" in txt
    assert "Validation Log" in txt


def test_plan_hint_is_advisory_not_auto_route():
    txt = _read("plan/SKILL.md").lower()
    assert "advisory" in txt
    assert "auto-route" in txt


# --- #3: contract-delta obligation (depth-1 caller) -------------------------

def test_code_review_scout_trigger_includes_contract_delta():
    txt = _read("code-review/SKILL.md")
    # the edge-case scout trigger must fire on BOTH the file-count and the
    # contract-delta condition (brief keeps both, does not replace)
    assert "Edge-case scout" in txt
    assert "≥3 files" in txt
    assert "contract-delta" in txt


def test_contract_delta_reference_exists_and_bounds_depth():
    txt = _read("code-review/references/contract-delta.md")
    assert "depth-1" in txt
    assert "grep" in txt and "call-site" in txt.lower()
    # name-honesty: advisory obligation, no automated detector
    low = txt.lower()
    assert "advisory" in low
    assert "no detector" in low or "not a detector" in low or "no automated detector" in low


def test_base_checklist_has_contract_delta_item():
    txt = _read("code-review/references/checklists/base.md")
    assert "Contract-delta" in txt


def test_scenario_has_contract_delta_dimension():
    txt = _read("scenario/SKILL.md")
    assert "contract-delta" in txt.lower()


# --- #2: verdict truth-table + dismissals store -----------------------------

def test_verdict_truth_table_reference_exists():
    txt = _read("code-review/references/verdict-truth-table.md")
    low = txt.lower()
    for v in ("confirmed", "dismissed", "needs-human"):
        assert v in low
    assert "code_evidence" in low
    # any condition FALSE => dismissed
    assert "dismissed" in low and "false" in low


def test_code_review_verdict_step_uses_truth_table():
    txt = _read("code-review/SKILL.md")
    low = txt.lower()
    for v in ("confirmed", "dismissed", "needs-human"):
        assert v in low
    assert "code_evidence" in low
    assert "verdict-truth-table.md" in txt
    # lookup-and-show against the dismissals store, never auto-hide
    assert "dismissals" in low


def test_base_suppressions_points_to_store_not_auto_suppress():
    txt = _read("code-review/references/checklists/base.md")
    low = txt.lower()
    assert "dismissals_store" in low or "dismissals store" in low
    # polarity-aware: require the NEGATION, not a bare 'auto-suppress' substring that
    # a positive sentence ("we auto-suppress noise") would also satisfy.
    assert "not auto-suppress" in low or "not** auto-suppress" in low \
        or "does not auto-suppress" in low


def test_critique_consolidation_has_batch_validate():
    txt = _read("critique/references/consolidation-contract.md")
    low = txt.lower()
    assert "batch-validate" in low or "batch validate" in low
    assert "truth-table" in low
    # only at critique (blinded multi-lens), not bolted onto default single review
    assert "single-reviewer" in low or "single reviewer" in low


# --- #5: contract-validation tier (catalog 4 probe) -------------------------

def test_contract_test_skill_catalogs_four_probes():
    txt = _read("contract-test/SKILL.md")
    for probe in ("API", "CLI", "Browser", "DB"):
        assert probe in txt, f"missing probe {probe}"


def test_probe_catalog_red_lines_and_staging():
    txt = _read("contract-test/references/probe-catalog.md")
    low = txt.lower()
    # the five red lines / safety model
    assert "not gate-driven" in low or "no gate-driven" in low or "never gate-driven" in low
    assert "review" in low and "shell" in low  # command reviewed before it hits the shell
    assert "stub" in low and "v2" in low  # Browser/DB deferred
    # R6: do not read the structural test as proof the injection path is closed
    assert "not_in_stage_requires" in txt
    assert "inherit" in low  # probe inherits tool-execution context permissions


def test_manual_test_points_to_contract_validation():
    txt = _read("manual-test/SKILL.md").lower()
    assert "contract-test" in txt or "contract-validation" in txt


# --- #7a: machine phase-DAG sidecar + checks --------------------------------

def test_phase_decomposition_describes_sidecar():
    txt = _read("plan/references/phase-decomposition.md")
    low = txt.lower()
    assert "plan-graph.yaml" in txt
    assert "edges" in low and "files_to_create" in low
    assert "no status" in low or "không status" in low or "not status" in low


def test_plan_skill_validate_runs_graph_checks_detection_only():
    txt = _read("plan/SKILL.md")
    low = txt.lower()
    assert "plan-graph.yaml" in txt or "plan_graph" in low
    assert "detection only" in low
    for fn in ("cycle", "parallel"):
        assert fn in low


def test_validate_gate_reads_sidecar_read_only():
    txt = _read("plan/references/validate-gate.md")
    low = txt.lower()
    assert "plan-graph.yaml" in txt or "plan_graph" in low
    assert "read-only" in low or "detection only" in low
