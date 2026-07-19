"""Tests for the four deterministic structural checks over the standards graph.

check_standards runs dangling_link, orphan, unaddressed_parent, and dep_cycle
(plus the folded-in invalid_id from the id-grammar framework and parse_error).
Pure structure, no judgment. The module always exits 0 — the gate does the
blocking. Severity catalog: dangling/orphan/dep_cycle/dep_dangling/invalid_id =
error; unaddressed_parent/orphan_arch_goal = warn.
"""

import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_standards  # noqa: E402
import standards_graph  # noqa: E402


# ── fixture builders ─────────────────────────────────────────────────────────

_VISION = "---\nid: VISION\ntype: vision\nstatus: approved\n---\n# Vision\n"
_STACK = "---\nid: STACK\ntype: stack\nstatus: approved\n---\n# Stack\n"


def _charter(extra_goals="") -> str:
    return f"""---
id: CHARTER
type: charter
goals:
  - id: ARCH-G1
    title: "Observability"
    status: approved
    metrics: [coverage]
{extra_goals}---

# Charter
"""


def _write_tree(root: Path, area_body: str, charter_extra="") -> Path:
    std = root / "harness" / "standards"
    (std / "areas").mkdir(parents=True, exist_ok=True)
    (std / "vision.md").write_text(_VISION, encoding="utf-8")
    (std / "STACK.md").write_text(_STACK, encoding="utf-8")
    (std / "charter.md").write_text(_charter(charter_extra), encoding="utf-8")
    (std / "areas" / "STD-AUTH.md").write_text(area_body, encoding="utf-8")
    return root


_VALID_AREA = """---
id: STD-AUTH
type: std_area
status: approved
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-AUTH-RG1
    title: "Sessions"
    status: approved
    rules:
      - id: STD-AUTH-RG1-R1
        title: "Expire in 24h"
        status: approved
        compliance_checks: ["TTL <= 24h"]
---
# Auth
"""


def _checks(graph):
    return check_standards.check(graph)


def _by(findings, check):
    return [f for f in findings if f["check"] == check]


# ── tests ────────────────────────────────────────────────────────────────────

def test_dangling_link_caught(tmp_path):
    # a rule_group referencing a non-existent std_area: rename the area id so the
    # rule_group's std_area parent points at a ghost. Simplest: a rule whose
    # rule_group does not exist.
    area = """---
id: STD-AUTH
type: std_area
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-AUTH-RG1
    title: "Sessions"
    rules:
      - id: STD-AUTH-RG1-R1
        title: "x"
        depends_on: [STD-GHOST-RG9-R9]
---
# Auth
"""
    root = _write_tree(tmp_path, area)
    graph = standards_graph.build_graph(root)
    findings = _checks(graph)
    # the depends_on target does not resolve → dep_dangling (the dangling family)
    dd = _by(findings, "dep_dangling")
    assert dd, "unresolved depends_on must surface as dep_dangling"
    assert dd[0]["severity"] == "error"
    assert dd[0]["context"]["ref"] == "STD-GHOST-RG9-R9"


def test_dangling_parent_link_caught(tmp_path):
    # an std_area referencing a non-existent arch_goal → dangling_link
    area = """---
id: STD-AUTH
type: std_area
arch_goals: [ARCH-G99]
rule_groups:
  - id: STD-AUTH-RG1
    title: "g"
    rules:
      - id: STD-AUTH-RG1-R1
        title: "x"
---
# Auth
"""
    root = _write_tree(tmp_path, area)
    graph = standards_graph.build_graph(root)
    dl = _by(_checks(graph), "dangling_link")
    assert dl, "std_area referencing a ghost arch_goal must be dangling_link"
    assert dl[0]["severity"] == "error"


def test_orphan_caught(tmp_path):
    # a rule with no rule_group reference at all → orphan_rule (distinct from dangling)
    # add a free-floating rule via a second rule_group with empty id so its rule
    # has no resolvable parent — but cleaner: a rule_group with no std_area is the
    # orphan path. Build an std_area with empty arch_goals to get orphan_std_area.
    area2 = """---
id: STD-AUTH
type: std_area
arch_goals: []
rule_groups:
  - id: STD-AUTH-RG1
    title: "g"
    rules:
      - id: STD-AUTH-RG1-R1
        title: "x"
---
# Auth
"""
    root = _write_tree(tmp_path, area2)
    graph = standards_graph.build_graph(root)
    findings = _checks(graph)
    orphans = _by(findings, "orphan_std_area")
    assert orphans, "std_area with empty arch_goals must be orphan_std_area"
    assert orphans[0]["severity"] == "error"
    # and it is NOT reported as dangling (no reference was declared)
    assert not _by(findings, "dangling_link")


def test_unaddressed_parent_caught(tmp_path):
    # an std_area with zero rule_groups → unaddressed_parent (warn);
    # an arch_goal with zero std_areas → orphan_arch_goal (warn).
    area = """---
id: STD-AUTH
type: std_area
arch_goals: [ARCH-G1]
rule_groups: []
---
# Auth
"""
    # ARCH-G2 has no std_area addressing it → orphan_arch_goal
    extra = "  - id: ARCH-G2\n    title: \"unused\"\n    status: approved\n    metrics: [x]\n"
    root = _write_tree(tmp_path, area, charter_extra=extra)
    graph = standards_graph.build_graph(root)
    findings = _checks(graph)
    up = _by(findings, "unaddressed_parent")
    assert any(f["artifact_id"] == "STD-AUTH" for f in up), "std_area with no rule_group → unaddressed_parent"
    assert all(f["severity"] == "warn" for f in up)
    oag = _by(findings, "orphan_arch_goal")
    assert any(f["artifact_id"] == "ARCH-G2" for f in oag)
    assert all(f["severity"] == "warn" for f in oag)


def test_dep_cycle_caught(tmp_path):
    area = """---
id: STD-AUTH
type: std_area
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-AUTH-RG1
    title: "g"
    rules:
      - id: STD-AUTH-RG1-R1
        title: "a"
        depends_on: [STD-AUTH-RG1-R2]
      - id: STD-AUTH-RG1-R2
        title: "b"
        depends_on: [STD-AUTH-RG1-R1]
---
# Auth
"""
    root = _write_tree(tmp_path, area)
    graph = standards_graph.build_graph(root)
    findings = _checks(graph)
    cyc = _by(findings, "dep_cycle")
    assert cyc, "circular depends_on must surface as dep_cycle"
    assert cyc[0]["severity"] == "error"
    path = cyc[0]["context"]["cycle"]
    assert path[0] == path[-1]  # closed path


def test_dep_cycle_long_chain_no_recursionerror(tmp_path):
    # build a ~2000-node linear depends_on chain inside one rule_group
    rules = []
    for i in range(2000):
        dep = f"        depends_on: [STD-AUTH-RG1-R{i + 1}]\n" if i < 1999 else ""
        rules.append(f"      - id: STD-AUTH-RG1-R{i}\n        title: \"r{i}\"\n{dep}")
    area = ("---\nid: STD-AUTH\ntype: std_area\narch_goals: [ARCH-G1]\n"
            "rule_groups:\n  - id: STD-AUTH-RG1\n    title: g\n    rules:\n"
            + "".join(rules) + "---\n# Auth\n")
    root = _write_tree(tmp_path, area)
    graph = standards_graph.build_graph(root)
    findings = _checks(graph)  # must not raise RecursionError
    assert not _by(findings, "dep_cycle"), "a linear chain has no cycle"


def test_invalid_id_not_crash(tmp_path):
    area = _VALID_AREA.replace("STD-AUTH-RG1-R1", "bogus rule id")
    root = _write_tree(tmp_path, area)
    graph = standards_graph.build_graph(root)
    findings = _checks(graph)  # must complete
    assert _by(findings, "invalid_id"), "garbage id must surface as invalid_id"


def test_duplicate_id_caught(tmp_path):
    # two rules declaring the same id collapse to one entry in any id-set, so a
    # copy-pasted duplicate can silently mask a missing node (dangling/orphan then
    # pass wrongly). duplicate_id names the collision before that masking happens.
    area = """---
id: STD-AUTH
type: std_area
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-AUTH-RG1
    title: g
    rules:
      - id: STD-AUTH-RG1-R1
        title: a
      - id: STD-AUTH-RG1-R1
        title: b
---
# Auth
"""
    root = _write_tree(tmp_path, area)
    graph = standards_graph.build_graph(root)
    findings = _checks(graph)
    dups = _by(findings, "duplicate_id")
    assert dups, "two nodes sharing an id must surface as duplicate_id"
    assert dups[0]["severity"] == "error"
    assert dups[0]["artifact_id"] == "STD-AUTH-RG1-R1"
    # one finding per colliding id, not one per duplicate node
    assert len([d for d in dups if d["artifact_id"] == "STD-AUTH-RG1-R1"]) == 1


def test_clean_tree_no_duplicate_id(tmp_path):
    root = _write_tree(tmp_path, _VALID_AREA)
    findings = _checks(standards_graph.build_graph(root))
    assert not _by(findings, "duplicate_id"), "unique ids must not flag duplicate_id"


def test_clean_tree_no_error_findings(tmp_path):
    root = _write_tree(tmp_path, _VALID_AREA)
    graph = standards_graph.build_graph(root)
    findings = _checks(graph)
    errors = [f for f in findings if f["severity"] == "error"]
    assert errors == [], f"clean tree must have zero error findings, got {errors}"


def test_bare_string_parent_no_phantom_findings(tmp_path):
    # a bare-string arch_goals on an std_area must not char-split into phantom
    # single-char dangling findings.
    area = """---
id: STD-AUTH
type: std_area
arch_goals: ARCH-G1
rule_groups:
  - id: STD-AUTH-RG1
    title: g
    rules:
      - id: STD-AUTH-RG1-R1
        title: x
---
# Auth
"""
    root = _write_tree(tmp_path, area)
    graph = standards_graph.build_graph(root)
    findings = _checks(graph)
    # no dangling finding should reference a single character
    for f in _by(findings, "dangling_link"):
        ref = (f.get("context") or {}).get("ref", "")
        assert len(ref) > 1, f"phantom single-char dangling ref: {ref!r}"


def test_deterministic_findings(tmp_path):
    root = _write_tree(tmp_path, _VALID_AREA)
    graph = standards_graph.build_graph(root)
    f1 = json.dumps(_checks(graph), sort_keys=True)
    f2 = json.dumps(_checks(graph), sort_keys=True)
    assert f1 == f2, "findings must be byte-stable across runs"


def test_always_exit_zero(tmp_path):
    # broken tree via subprocess → exit 0, JSON on stdout
    area = _VALID_AREA.replace("rule_group", "rule_group").replace(
        "STD-AUTH-RG1-R1", "STD-AUTH-RG1-R1\n        depends_on: [GHOST]")
    root = _write_tree(tmp_path, area)
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_standards.py"), "--root", str(root)],
        capture_output=True, text=True)
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert "findings" in out and "graph" in out


def test_check_tolerates_list_valued_scalar_parent_link():
    # The builder coerces a non-scalar parent link to None, so the CLI/gate path
    # never reaches check() with a list here. A direct library caller can, though:
    # a list ref is unhashable, and the scalar membership test must not crash on
    # it. Mirror the std_area branch — a bad shape is skipped, not flagged.
    graph = {
        "nodes": [{"id": "STD-X-RG1-R1", "type": "rule",
                   "rule_group": ["STD-X-RG1"]}],
        "edges": [],
    }
    findings = check_standards.check(graph)  # must not raise TypeError
    assert not _by(findings, "dangling_link")
