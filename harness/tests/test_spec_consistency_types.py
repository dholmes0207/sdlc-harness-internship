"""check_consistency LIST_FIELDS invalid_type parity across all 5 list fields.

Regression lock: the node builder (spec_graph) and check_consistency's AC
enrichment used to coerce a falsy scalar (e.g. `personas: 0`) to an empty list
with `or []` BEFORE the LIST_FIELDS check ran, so only `risks` (which never had
the `or []`) ever got flagged `invalid_type` — the sibling fields silently
passed malformed frontmatter through undetected. All 5 fields must now behave
the same way: a non-list scalar is flagged, an absent field is not.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Literal path keeps the stashed-skill collect_ignore coupling working:
# harness/plugins/hs/skills/spec/scripts
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402
from conftest import make_proj  # noqa: E402

_NAMES = [
    "encoding_utils", "id_grammar", "frontmatter_parser", "spec_graph", "dec_ledger",
    "check_consistency_schema", "check_consistency_time", "check_consistency_risk",
    "check_consistency_competition", "session_staleness", "check_consistency_product",
    "check_consistency",
]
_mods = load_skill_scripts(_SPEC_SCRIPTS, _NAMES)
spec_graph = _mods["spec_graph"]
check_consistency = _mods["check_consistency"]


def _set_field_scalar(proj, field: str, value):
    """Override/insert `field: value` in the story's frontmatter. PyYAML's
    safe_load keeps the LAST occurrence of a duplicate mapping key, so the
    override is inserted right before the CLOSING `---` delimiter — after any
    existing declaration of `field` further up (e.g. the fixture's
    multi-line `acceptance_criteria:` list) — so it always wins regardless of
    where in the frontmatter `field` already lives."""
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    text = story.read_text(encoding="utf-8")
    marker = "---\n"
    first = text.index(marker)
    second = text.index(marker, first + len(marker))
    story.write_text(text[:second] + f"{field}: {value}\n" + text[second:], encoding="utf-8")


def _invalid_type_findings(proj):
    graph = spec_graph.build_graph(proj)
    check_consistency._enrich_with_ac(graph, proj)
    findings = check_consistency.check(graph)
    return [f for f in findings if f["check"] == "invalid_type"]


@pytest.mark.parametrize("field", ["personas", "metrics", "brd_goals", "acceptance_criteria", "risks"])
def test_falsy_scalar_list_field_flagged_invalid_type(tmp_path, field):
    proj = make_proj(tmp_path)
    _set_field_scalar(proj, field, 0)
    hits = [f for f in _invalid_type_findings(proj)
            if f.get("context") and f["context"].get("field") == field]
    assert hits, f"{field}: 0 must be flagged invalid_type, matching risks's behavior"
    assert hits[0]["severity"] == "error"


def test_personas_and_acceptance_criteria_both_flagged_together(tmp_path):
    # The reviewer's exact repro: both fields set to a falsy scalar on the same
    # story must both surface invalid_type (not just one of them).
    proj = make_proj(tmp_path)
    _set_field_scalar(proj, "personas", 0)
    _set_field_scalar(proj, "acceptance_criteria", 0)
    fields = {f["context"]["field"] for f in _invalid_type_findings(proj) if f.get("context")}
    assert {"personas", "acceptance_criteria"} <= fields


def test_acceptance_criteria_scalar_also_surfaces_invalid_type(tmp_path):
    # Before the fix, `or []` in _enrich_with_ac coerced `acceptance_criteria: 0`
    # to [] BEFORE the LIST_FIELDS check ran, so the malformed frontmatter was
    # entirely invisible to invalid_type (only the downstream missing_ac fired,
    # which resolve_ac's own list-guard still degrades a scalar to — that part
    # is unchanged and correct). The fix makes invalid_type surface alongside it.
    proj = make_proj(tmp_path)
    _set_field_scalar(proj, "acceptance_criteria", 0)
    graph = spec_graph.build_graph(proj)
    check_consistency._enrich_with_ac(graph, proj)
    findings = check_consistency.check(graph)
    checks = {f["check"] for f in findings}
    assert "invalid_type" in checks


def test_absent_list_field_not_flagged(tmp_path):
    # An absent field (not present at all) is still fine on any of the 5 —
    # LIST_FIELDS's `if v is None: continue` guard must keep passing.
    proj = make_proj(tmp_path)
    hits = _invalid_type_findings(proj)
    assert hits == []


# ---------------------------------------------------------------------------
# PRODUCT-level personas: the PRODUCT node's `personas` (via _node_from_artifact)
# was already raw pass-through (flagged invalid_type above like every other
# LIST_FIELDS field), but graph["product"]["personas"] (the separate top-level
# meta block _product_meta() builds, which render_ascii.persona() actually
# reads) coerced a falsy scalar to [] with an `or []` -- so a malformed
# `personas: 0` surfaced as an error on one view and a silent, clean "no
# personas" on the other. _product_meta must raw-pass-through too, matching
# the node path, so both views see the same underlying value.
# ---------------------------------------------------------------------------

def _set_product_field_scalar(proj, field: str, value):
    product = proj / "docs" / "product" / "PRODUCT.md"
    text = product.read_text(encoding="utf-8")
    marker = "---\n"
    first = text.index(marker)
    second = text.index(marker, first + len(marker))
    product.write_text(text[:second] + f"{field}: {value}\n" + text[second:], encoding="utf-8")


def test_product_personas_scalar_flagged_invalid_type_on_node(tmp_path):
    proj = make_proj(tmp_path)
    _set_product_field_scalar(proj, "personas", 0)
    hits = [f for f in _invalid_type_findings(proj)
            if f.get("context") and f["context"].get("field") == "personas"
            and f.get("artifact_id") == "PRODUCT"]
    assert hits, "PRODUCT.md personas: 0 must be flagged invalid_type on the PRODUCT node"


def test_product_meta_personas_is_raw_passthrough_not_or_masked(tmp_path):
    proj = make_proj(tmp_path)
    _set_product_field_scalar(proj, "personas", 0)
    graph = spec_graph.build_graph(proj)
    # Before the fix this was [] (the `or []` masked the malformed scalar);
    # it must now carry the real value so it agrees with the node's own
    # (already-raw) personas field instead of silently looking clean.
    assert graph["product"]["personas"] == 0


# ---------------------------------------------------------------------------
# BRD-goal `metrics` (via _node_from_goal) had the same `or []` mask: a scalar
# `metrics: 0` coerced to [] BEFORE the LIST_FIELDS check ran, so a malformed
# type surfaced as the LESS-accurate goal_without_metric ("no metric at all")
# instead of invalid_type ("wrong shape"). Raw pass-through on the goal node
# makes invalid_type own the shape error (the established rule), and check_goals
# defers -- so a wrong-type metric is NOT also mislabeled as absent.
# ---------------------------------------------------------------------------

def _set_goal_metrics_scalar(proj, value):
    brd = proj / "docs" / "product" / "brd.md"
    text = brd.read_text(encoding="utf-8")
    text = text.replace("    metrics: [arr]", f"    metrics: {value}", 1)
    brd.write_text(text, encoding="utf-8")


def test_goal_metrics_scalar_flagged_invalid_type(tmp_path):
    proj = make_proj(tmp_path)
    _set_goal_metrics_scalar(proj, 0)
    hits = [f for f in _invalid_type_findings(proj)
            if f.get("context") and f["context"].get("field") == "metrics"
            and f.get("artifact_id") == "BRD-G1"]
    assert hits, "BRD goal metrics: 0 must be flagged invalid_type on the goal node"


def test_goal_metrics_scalar_not_mislabeled_goal_without_metric(tmp_path):
    proj = make_proj(tmp_path)
    _set_goal_metrics_scalar(proj, 0)
    graph = spec_graph.build_graph(proj)
    check_consistency._enrich_with_ac(graph, proj)
    findings = check_consistency.check(graph)
    # invalid_type owns the shape error; goal_without_metric (absence) must NOT
    # also fire for the same goal -- that would double-report one defect.
    gwm = [f for f in findings
           if f["check"] == "goal_without_metric" and f.get("artifact_id") == "BRD-G1"]
    assert gwm == [], "wrong-type metric must defer to invalid_type, not mislabel as absent"


def _add_goal_stray_key(proj, line):
    brd = proj / "docs" / "product" / "brd.md"
    text = brd.read_text(encoding="utf-8")
    text = text.replace("    metrics: [arr]", f"    metrics: [arr]\n    {line}", 1)
    brd.write_text(text, encoding="utf-8")


def _set_prd_field_scalar(proj, field, value):
    prd = proj / "docs" / "product" / "prds" / "auth.md"
    text = prd.read_text(encoding="utf-8")
    marker = "---\n"
    first = text.index(marker)
    second = text.index(marker, first + len(marker))
    prd.write_text(text[:second] + f"{field}: {value}\n" + text[second:], encoding="utf-8")


def test_depends_on_bare_scalar_flagged_invalid_type_not_silently_dropped(tmp_path):
    # `depends_on: PRD-X` (brackets forgotten) is a non-list scalar. It is coerced
    # to [] on the node so the adjacency/render consumers never char-split a bare
    # string -- but the malformed shape must still surface invalid_type, exactly
    # like the sibling id-lists brd_goals/serves. Before the fix it vanished with
    # ZERO findings and passed --strict clean, dropping the real edge.
    proj = make_proj(tmp_path)
    _set_prd_field_scalar(proj, "depends_on", "PRD-GHOST")
    hits = [f for f in _invalid_type_findings(proj)
            if f.get("context", {}).get("field") == "depends_on"
            and f.get("artifact_id") == "PRD-AUTH"]
    assert hits, "bare-scalar depends_on silently coerced to [] with no invalid_type finding"


def test_goal_stray_legacy_metric_key_not_swallowed_when_metrics_present(tmp_path):
    # A stray legacy `metric:` alongside a proper `metrics:` list is NOT the
    # actionable migrate case (the metric block is skipped because metrics exist),
    # so the stray-key loop must NOT unconditionally `continue` past it and swallow
    # it -- it falls through to unknown_goal_key instead.
    proj = make_proj(tmp_path)
    _add_goal_stray_key(proj, "metric: legacy-slug")
    graph = spec_graph.build_graph(proj)
    check_consistency._enrich_with_ac(graph, proj)
    findings = check_consistency.check(graph)
    hits = [f for f in findings
            if f.get("artifact_id") == "BRD-G1"
            and f.get("context", {}).get("key") == "metric"]
    assert hits, "stray `metric:` key swallowed when a proper `metrics:` list co-exists"
