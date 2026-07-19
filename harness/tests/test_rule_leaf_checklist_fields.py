"""Tests for the std rule-leaf checklist fields + the operational (H2) zone.

A rule-leaf in the standards tree now carries six checklist fields
(scope / severity / enabled / floor / relates_to_std / detector) so the same
YAML node both guides code (charter prose) and drives review (operational
checklist). A std-area declares a `zone` (charter | operational); the renderer
keeps the two zones in separate digests — the org-charter digest never mixes the
operational review checklist (H2 zone split).

Back-compat: a legacy rule with none of the new fields still builds, with safe
defaults (enabled:true, floor:false, scope:[], severity:info, relates_to_std:[],
detector:null).
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import standards_graph  # noqa: E402
import render_standards  # noqa: E402
import standards_strict_gate  # noqa: E402


_CHARTER = """id: CHARTER
type: charter
goals:
  - id: ARCH-G1
    title: "Secure services"
    metrics: [x]
"""

_AREA_LEGACY = """id: STD-AUTH
type: std_area
title: "Auth"
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-AUTH-RG1
    title: "Sessions"
    rules:
      - id: STD-AUTH-RG1-R1
        title: "Sessions expire"
        compliance_checks: ["assert ttl"]
"""

_AREA_FULL = """id: STD-AUTH
type: std_area
title: "Auth"
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-AUTH-RG1
    title: "Sessions"
    rules:
      - id: STD-AUTH-RG1-R1
        title: "Sessions expire"
        scope: ["**/*.py", "src/**"]
        severity: critical
        enabled: true
        floor: true
        relates_to_std: [STD-AUTH-RG1-R2]
        detector: {pattern: "session", type: regex}
"""

_OPERATIONAL = """id: STD-REVIEW-PY
type: std_area
zone: operational
title: "Python Review"
rule_groups:
  - id: STD-REVIEW-PY-RG1
    title: "Discards"
    rules:
      - id: STD-REVIEW-PY-RG1-R1
        title: "No bare except"
        scope: ["**/*.py"]
        severity: critical
        floor: true
        detector: {pattern: "except:"}
"""


def _build(root, area_text, charter_text=None, area_name="STD-AREA"):
    std = root / "harness" / "standards"
    (std / "areas").mkdir(parents=True, exist_ok=True)
    if charter_text:
        (std / "charter.std.yaml").write_text(charter_text, encoding="utf-8")
    (std / "areas" / (area_name + ".std.yaml")).write_text(area_text, encoding="utf-8")
    return standards_graph.build_graph(root)


def _rule_node(graph, rid):
    return next(n for n in graph["nodes"] if n.get("id") == rid)


# --- Tests Before: lock existing (a legacy rule still builds, safe defaults) ---

def test_rule_leaf_legacy_builds(tmp_path):
    g = _build(tmp_path, _AREA_LEGACY, _CHARTER)
    r = _rule_node(g, "STD-AUTH-RG1-R1")
    assert r["type"] == "rule"
    assert r["title"] == "Sessions expire"
    assert r["scope"] == []
    assert r["severity"] == "info"
    assert r["enabled"] is True
    assert r["floor"] is False
    assert r["relates_to_std"] == []
    assert r["detector"] is None


# --- Tests After: the six checklist fields parse + coerce ---

def test_rule_leaf_checklist_fields_parsed(tmp_path):
    g = _build(tmp_path, _AREA_FULL, _CHARTER)
    r = _rule_node(g, "STD-AUTH-RG1-R1")
    assert r["scope"] == ["**/*.py", "src/**"]
    assert r["severity"] == "critical"
    assert r["enabled"] is True
    assert r["floor"] is True
    assert r["relates_to_std"] == ["STD-AUTH-RG1-R2"]
    assert isinstance(r["detector"], dict) and r["detector"]["pattern"] == "session"


def test_severity_coerces_invalid_to_info(tmp_path):
    bad = _AREA_FULL.replace("severity: critical", "severity: bogus")
    g = _build(tmp_path, bad, _CHARTER)
    assert _rule_node(g, "STD-AUTH-RG1-R1")["severity"] == "info"


def test_malformed_fields_never_raise(tmp_path):
    # a non-list scope / non-bool floor / list detector all coerce to safe values
    bad = _AREA_FULL.replace('scope: ["**/*.py", "src/**"]', "scope: not-a-list")
    bad = bad.replace("floor: true", "floor: maybe")
    bad = bad.replace('detector: {pattern: "session", type: regex}', "detector: [1, 2]")
    g = _build(tmp_path, bad, _CHARTER)
    r = _rule_node(g, "STD-AUTH-RG1-R1")
    assert r["scope"] == []
    assert r["floor"] is False
    assert r["detector"] is None


# --- [RED-TEAM F8] floor round-trip persistence ---

def test_floor_roundtrip_persisted(tmp_path):
    # floor:true survives build -> render (shown), and dropping it reverts to
    # false AND changes content_hash (the field is not masked at any path).
    g = _build(tmp_path, _OPERATIONAL, area_name="STD-REVIEW-PY")
    r = _rule_node(g, "STD-REVIEW-PY-RG1-R1")
    assert r["floor"] is True
    digest = render_standards.render_operational(g)
    assert "STD-REVIEW-PY-RG1-R1" in digest
    assert "floor" in digest.lower()

    g2 = _build(tmp_path, _OPERATIONAL.replace("floor: true", "floor: false"),
                area_name="STD-REVIEW-PY")
    r2 = _rule_node(g2, "STD-REVIEW-PY-RG1-R1")
    assert r2["floor"] is False
    assert r2["content_hash"] != r["content_hash"]


# --- operational zone: valid ID grammar, no orphan via the strict gate ---

def test_operational_area_id_grammar(tmp_path):
    std = tmp_path / "harness" / "standards"
    (std / "areas").mkdir(parents=True, exist_ok=True)
    (std / "areas" / "STD-REVIEW-PY.std.yaml").write_text(_OPERATIONAL, encoding="utf-8")
    findings = standards_strict_gate.core(tmp_path)
    errors = [f for f in findings if f.get("severity") == "error"]
    assert errors == [], errors


# --- render: operational checklist is separate from the charter digest ---

def test_render_operational_checklist(tmp_path):
    std = tmp_path / "harness" / "standards"
    (std / "areas").mkdir(parents=True, exist_ok=True)
    (std / "charter.std.yaml").write_text(_CHARTER, encoding="utf-8")
    (std / "areas" / "STD-AUTH.std.yaml").write_text(_AREA_FULL, encoding="utf-8")
    (std / "areas" / "STD-REVIEW-PY.std.yaml").write_text(_OPERATIONAL, encoding="utf-8")
    g = standards_graph.build_graph(tmp_path)

    charter = render_standards.render_graph(g)
    op = render_standards.render_operational(g)

    # operational rule in the operational checklist, NOT the charter digest
    assert "STD-REVIEW-PY-RG1-R1" in op
    assert "STD-REVIEW-PY-RG1-R1" not in charter
    # charter rule in the charter digest, NOT the operational checklist
    assert "STD-AUTH-RG1-R1" in charter
    assert "STD-AUTH-RG1-R1" not in op
    # checklist surfaces scope + severity + per-lang grouping
    assert "**/*.py" in op
    assert "critical" in op
    assert "python" in op.lower()
    # deterministic
    assert render_standards.render_operational(g) == op


def test_render_operational_check_drift(tmp_path):
    std = tmp_path / "harness" / "standards"
    (std / "areas").mkdir(parents=True, exist_ok=True)
    (std / "areas" / "STD-REVIEW-PY.std.yaml").write_text(_OPERATIONAL, encoding="utf-8")
    out = tmp_path / "checklist.md"
    assert render_standards.main(
        ["--root", str(tmp_path), "--out", str(out), "--kind", "operational"]) == 0
    assert render_standards.main(
        ["--root", str(tmp_path), "--out", str(out), "--kind", "operational", "--check"]) == 0
    # flip floor -> the checklist changes -> --check flags drift
    area = std / "areas" / "STD-REVIEW-PY.std.yaml"
    area.write_text(area.read_text(encoding="utf-8").replace(
        "floor: true", "floor: false"), encoding="utf-8")
    assert render_standards.main(
        ["--root", str(tmp_path), "--out", str(out), "--kind", "operational", "--check"]) == 1


# --- schema documents the new fields ---
