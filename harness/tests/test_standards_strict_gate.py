"""Tests for the thin standards strict gate.

standards_strict_gate re-runs the structural checks and exits non-zero (2, the
harness block convention) when any finding is severity=error, exits 0 on a clean
tree, and always writes a human summary to stderr. Warns never block. A
core(root) library entry returns the same findings the checks would, so a future
compliance hook can wrap the same logic. The fail-closed path is tested via
subprocess + real exit codes.
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import subprocess  # noqa: E402

_GATE = _SCRIPTS / "standards_strict_gate.py"

_VISION = "---\nid: VISION\ntype: vision\nstatus: approved\n---\n# Vision\n"
_STACK = "---\nid: STACK\ntype: stack\nstatus: approved\n---\n# Stack\n"
_CHARTER = """---
id: CHARTER
type: charter
goals:
  - id: ARCH-G1
    title: "Observability"
    status: approved
    metrics: [coverage]
---
# Charter
"""

_VALID_AREA = """---
id: STD-AUTH
type: std_area
status: approved
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-AUTH-RG1
    title: "Sessions"
    rules:
      - id: STD-AUTH-RG1-R1
        title: "Expire"
        compliance_checks: ["TTL <= 24h"]
---
# Auth
"""

# warn-only: an std_area with no rule_groups (unaddressed_parent warn), no errors
_WARN_AREA = """---
id: STD-AUTH
type: std_area
status: approved
arch_goals: [ARCH-G1]
rule_groups: []
---
# Auth
"""

# error: a rule depending on a ghost (dep_dangling error)
_ERROR_AREA = """---
id: STD-AUTH
type: std_area
status: approved
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-AUTH-RG1
    title: "Sessions"
    rules:
      - id: STD-AUTH-RG1-R1
        title: "Expire"
        depends_on: [STD-GHOST-RG9-R9]
---
# Auth
"""


def _write_tree(root: Path, area_body: str) -> Path:
    std = root / "harness" / "standards"
    (std / "areas").mkdir(parents=True, exist_ok=True)
    (std / "vision.md").write_text(_VISION, encoding="utf-8")
    (std / "STACK.md").write_text(_STACK, encoding="utf-8")
    (std / "charter.md").write_text(_CHARTER, encoding="utf-8")
    (std / "areas" / "STD-AUTH.md").write_text(area_body, encoding="utf-8")
    return root


def _run(root: Path, env_extra=None):
    import os
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_GATE), "--root", str(root)],
        capture_output=True, text=True, env=env)


def _guard_policy(root: Path, *, preset="balanced", standards=None):
    """Hermetic guard-policy.yaml; `standards` overrides standards_strict_gate
    so a test can pin the gate to warn/off independent of the shipped file."""
    lines = ['schema_version: "1.0"', 'preset: "%s"' % preset]
    if standards is not None:
        lines += ["overrides:", '  standards_strict_gate: "%s"' % standards]
    else:
        lines.append("overrides: {}")
    p = root / "guard-policy.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_exit_zero_on_clean_tree(tmp_path):
    root = _write_tree(tmp_path, _VALID_AREA)
    proc = _run(root)
    assert proc.returncode == 0
    assert "standards_strict_gate" in proc.stderr


def test_exit_two_on_error_finding(tmp_path):
    root = _write_tree(tmp_path, _ERROR_AREA)
    proc = _run(root, {"HARNESS_GUARD_POLICY": str(_guard_policy(tmp_path))})
    assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}: {proc.stderr}"
    assert "BLOCKED" in proc.stderr
    assert "dep_dangling" in proc.stderr


def test_error_warn_downgrades_to_advisory_exit_zero(tmp_path):
    # standards_strict_gate=warn: an error finding no longer blocks; the BLOCKED
    # detail is emitted as an advisory and the gate exits 0.
    root = _write_tree(tmp_path, _ERROR_AREA)
    proc = _run(root, {"HARNESS_GUARD_POLICY":
                       str(_guard_policy(tmp_path, standards="warn"))})
    assert proc.returncode == 0, f"warn must not block: {proc.stderr}"
    assert "[advisory]" in proc.stderr
    assert "dep_dangling" in proc.stderr


def test_error_off_is_silent_exit_zero(tmp_path):
    root = _write_tree(tmp_path, _ERROR_AREA)
    proc = _run(root, {"HARNESS_GUARD_POLICY":
                       str(_guard_policy(tmp_path, standards="off"))})
    assert proc.returncode == 0, f"off must not block: {proc.stderr}"
    assert "BLOCKED" not in proc.stderr


def test_warn_only_does_not_block(tmp_path):
    root = _write_tree(tmp_path, _WARN_AREA)
    proc = _run(root)
    assert proc.returncode == 0, f"warn-only must not block: {proc.stderr}"


def test_missing_tree_does_not_block(tmp_path):
    # no harness/standards/ at all → no nodes → no errors → exit 0
    proc = _run(tmp_path)
    assert proc.returncode == 0


def test_core_returns_findings(tmp_path):
    import standards_strict_gate
    import check_standards
    import standards_graph
    root = _write_tree(tmp_path, _ERROR_AREA)
    via_core = standards_strict_gate.core(root)
    direct = check_standards.check(standards_graph.build_graph(root))
    assert via_core == direct, "core(root) must match check() output (the hook seam)"
