"""Tests for render_standards — SSOT-YAML standards tree -> code-standards.md digest.

The renderer is the secondary representation: one direction, SSOT -> view, never
the reverse. It is deterministic (sorted by id, no timestamp) so re-rendering the
same tree is byte-identical, and a `--check` mode diffs the rendered output
against a committed file to catch drift in CI. `--out` is required (no implicit
default that could wipe a hand-authored file) and writing refuses to clobber a
file lacking the generated-marker unless --force.
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import render_standards  # noqa: E402


_CHARTER = """id: CHARTER
type: charter
status: approved
owner: arch-team
version: 1.0.0
goals:
  - id: ARCH-G1
    title: "Observable services"
    status: approved
    owner: arch-team
    metrics: [trace-coverage]
"""

_AREA = """id: STD-AUTH
type: std_area
title: "Authentication Standards"
status: approved
owner: security-team
version: 1.0.0
arch_goals: [ARCH-G1]
description: |
  How services authenticate and manage sessions.
rule_groups:
  - id: STD-AUTH-RG1
    title: "Session handling"
    status: approved
    owner: security-team
    rules:
      - id: STD-AUTH-RG1-R1
        title: "Sessions expire after 24h"
        status: approved
        compliance_checks: ["assert session TTL <= 24h"]
        rationale: |
          Long-lived sessions widen the theft window.
"""


def _tree(root: Path) -> Path:
    std = root / "harness" / "standards"
    (std / "areas").mkdir(parents=True, exist_ok=True)
    (std / "charter.std.yaml").write_text(_CHARTER, encoding="utf-8")
    (std / "areas" / "STD-AUTH.std.yaml").write_text(_AREA, encoding="utf-8")
    return root


def test_render_empty_tree(tmp_path):
    # an empty tree renders a skeleton digest (marker present), never crashes
    (tmp_path / "harness" / "standards" / "areas").mkdir(parents=True)
    out = render_standards.render_root(tmp_path)
    assert render_standards.GENERATED_MARKER in out


def test_render_fixture_tree(tmp_path):
    root = _tree(tmp_path)
    out = render_standards.render_root(root)
    assert "Authentication Standards" in out
    assert "Session handling" in out
    assert "STD-AUTH-RG1-R1" in out
    assert "Sessions expire after 24h" in out
    assert "assert session TTL <= 24h" in out          # compliance_check rendered
    assert "Long-lived sessions widen the theft window" in out  # rationale rendered
    assert "How services authenticate" in out          # area description rendered


def test_render_check_dict_form_no_raw_repr():
    # a type-1 shell check ({type, cmd}) renders its cmd, not a Python dict repr
    assert render_standards._render_check(
        {"type": "shell", "cmd": "grep -q x f"}) == "grep -q x f"
    assert render_standards._render_check("plain string") == "plain string"
    assert "{" not in render_standards._render_check({"type": "shell", "cmd": "true"})


def test_render_idempotent(tmp_path):
    root = _tree(tmp_path)
    a = render_standards.render_root(root)
    b = render_standards.render_root(root)
    assert a == b                                       # byte-identical
    assert "\n" in a


def test_render_out_required(tmp_path, capsys):
    root = _tree(tmp_path)
    rc = render_standards.main(["--root", str(root)])   # no --out
    assert rc == 2
    assert "out" in capsys.readouterr().err.lower()


def test_render_writes_and_check_passes(tmp_path):
    root = _tree(tmp_path)
    out = root / "digest.md"
    assert render_standards.main(["--root", str(root), "--out", str(out)]) == 0
    assert render_standards.GENERATED_MARKER in out.read_text(encoding="utf-8")
    # immediately after writing, --check finds no drift
    assert render_standards.main(["--root", str(root), "--out", str(out), "--check"]) == 0


def test_check_mode_detects_drift(tmp_path):
    root = _tree(tmp_path)
    out = root / "digest.md"
    assert render_standards.main(["--root", str(root), "--out", str(out)]) == 0
    # mutate the SSOT without re-rendering -> --check must flag drift
    area = root / "harness" / "standards" / "areas" / "STD-AUTH.std.yaml"
    area.write_text(area.read_text(encoding="utf-8").replace(
        "Session handling", "Session lifecycle"), encoding="utf-8")
    assert render_standards.main(["--root", str(root), "--out", str(out), "--check"]) == 1


def test_no_clobber_hand_authored(tmp_path, capsys):
    root = _tree(tmp_path)
    out = root / "hand.md"
    out.write_text("# Hand-authored, no marker\n", encoding="utf-8")
    rc = render_standards.main(["--root", str(root), "--out", str(out)])
    assert rc == 2                                      # refuses to clobber
    assert "force" in capsys.readouterr().err.lower()
    # --force overrides
    assert render_standards.main(["--root", str(root), "--out", str(out), "--force"]) == 0
    assert render_standards.GENERATED_MARKER in out.read_text(encoding="utf-8")
