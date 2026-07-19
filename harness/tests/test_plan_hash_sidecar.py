"""plan_hash / file_hashes coverage of the phase-graph sidecar (option D).

The sidecar plan-graph.yaml is folded into the approved plan-hash so editing an edge
re-triggers the drift guard, yet it is RAW-hashed (no frontmatter strip) so a sidecar
that happens to open with `---` does not get swallowed to an empty string and collide on
the empty-sha digest. One special-case in _normalized_text covers both plan_hash and
file_hashes because both route through it.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plan_approval as pa  # noqa: E402

_EMPTY_SHA12 = "e3b0c44298fc"  # sha256("")[:12]

_PLAN_MD = (
    "---\ntitle: t\nphase_graph: plan-graph.yaml\n---\n\n"
    "# Plan\n\n## Phases\n\n- [ ] P1\n"
)
_PHASE_MD = "---\nphase: 1\n---\n\n# Phase 1\n\nbody\n"
# sidecar deliberately opens with `---` to exercise the empty-collision trap
_SIDE = "---\nedges:\n  - {from: P1, to: P2}\n"
_SIDE_EDITED = "---\nedges:\n  - {from: P1, to: P3}\n"


def _mk(tmp_path: Path, sidecar=_SIDE) -> Path:
    (tmp_path / "plan.md").write_text(_PLAN_MD, encoding="utf-8")
    (tmp_path / "phase-1.md").write_text(_PHASE_MD, encoding="utf-8")
    if sidecar is not None:
        (tmp_path / "plan-graph.yaml").write_text(sidecar, encoding="utf-8")
    return tmp_path


def test_sidecar_edit_changes_plan_hash(tmp_path):
    p = _mk(tmp_path)
    before = pa.plan_hash(p)
    (p / "plan-graph.yaml").write_text(_SIDE_EDITED, encoding="utf-8")
    assert pa.plan_hash(p) != before


def test_sidecar_edit_changes_file_hashes(tmp_path):
    p = _mk(tmp_path)
    before = pa.file_hashes(p)["plan-graph.yaml"]
    (p / "plan-graph.yaml").write_text(_SIDE_EDITED, encoding="utf-8")
    assert pa.file_hashes(p)["plan-graph.yaml"] != before


def test_sidecar_not_empty_digest(tmp_path):
    p = _mk(tmp_path)
    # raw-hashed: the `---`-opening sidecar must NOT collapse to the empty-sha digest
    assert pa.file_hashes(p)["plan-graph.yaml"] != _EMPTY_SHA12


def test_delete_sidecar_trips_drift(tmp_path):
    p = _mk(tmp_path)
    before = pa.plan_hash(p)
    (p / "plan-graph.yaml").unlink()
    assert pa.plan_hash(p) != before  # file dropped from the list → hash changes


def test_phases_subdir_covered_by_hash(tmp_path):
    """Phase files under phases/ (the current scaffold layout) fold into the plan
    hash — an edit there must trip drift exactly like a flat-layout phase. Guards
    the gap that let a phases/-layout plan's phase edits slip past approval."""
    (tmp_path / "plan.md").write_text(_PLAN_MD, encoding="utf-8")
    ph = tmp_path / "phases"
    ph.mkdir()
    (ph / "phase-1-scout.md").write_text(_PHASE_MD, encoding="utf-8")
    assert "phase-1-scout.md" in pa.file_hashes(tmp_path)
    before = pa.plan_hash(tmp_path)
    (ph / "phase-1-scout.md").write_text(
        _PHASE_MD.replace("body", "edited"), encoding="utf-8")
    assert pa.plan_hash(tmp_path) != before


def test_flat_and_phases_layouts_hash_equal_content(tmp_path):
    """A plan authored flat (phase-1.md at root) and one authored under phases/
    with identical phase content hash the SAME phase digest — layout is not
    content, so migrating a plan into phases/ does not spuriously trip drift on
    the phase file itself (only the plan-dir shape moved)."""
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "phase-1.md").write_text(_PHASE_MD, encoding="utf-8")
    nested = tmp_path / "nested"
    (nested / "phases").mkdir(parents=True)
    (nested / "phases" / "phase-1.md").write_text(_PHASE_MD, encoding="utf-8")
    assert (pa.file_hashes(flat)["phase-1.md"]
            == pa.file_hashes(nested)["phase-1.md"])


def test_name_hardcoded_not_from_frontmatter(tmp_path):
    # the hashed sidecar name is hardcoded; a frontmatter phase_graph: pointing
    # elsewhere must NOT redirect which file is hashed (no rename bypass).
    p = _mk(tmp_path)
    (p / "plan.md").write_text(
        _PLAN_MD.replace("plan-graph.yaml", "other.yaml"), encoding="utf-8")
    (p / "other.yaml").write_text("edges: []\n", encoding="utf-8")
    fh = pa.file_hashes(p)
    assert "plan-graph.yaml" in fh
    assert "other.yaml" not in fh
