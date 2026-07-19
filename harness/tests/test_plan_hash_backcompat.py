"""Back-compat: a plan with NO sidecar must hash exactly as it did before option D.

Marked dev_repo — it asserts the algorithm of the development tree's plan_approval.
The guarantee is structural: when plan-graph.yaml is absent, _plan_files appends
nothing (is_file() is False), so plan_hash is byte-identical to the old plan.md +
phase-*.md hash. We prove that by recomputing the old algorithm inline and comparing.
"""
import hashlib
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plan_approval as pa  # noqa: E402

pytestmark = pytest.mark.dev_repo

_PLAN_MD = "---\ntitle: t\n---\n\n# Plan\n\n## Phases\n\n- [ ] P1\n"
_PHASE_MD = "---\nphase: 1\n---\n\n# Phase 1\n\nbody\n"


def _mk_no_sidecar(tmp_path: Path) -> Path:
    (tmp_path / "plan.md").write_text(_PLAN_MD, encoding="utf-8")
    (tmp_path / "phase-1.md").write_text(_PHASE_MD, encoding="utf-8")
    return tmp_path


def _old_algorithm(plan_dir: Path) -> str:
    # the pre-option-D hash: plan.md + phase-*.md only, normalized, name-delimited
    files = [plan_dir / "plan.md"] + sorted(plan_dir.glob("phase-*.md"))
    h = hashlib.sha256()
    for f in files:
        if not f.is_file():
            continue
        h.update(f.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(pa._normalized_text(f).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def test_existing_plan_hash_unchanged(tmp_path):
    p = _mk_no_sidecar(tmp_path)
    assert pa.plan_hash(p) == _old_algorithm(p)


def test_plan_approval_keyset_with_sidecar(tmp_path):
    # with a sidecar present the file-hash keyset gains exactly plan-graph.yaml
    p = _mk_no_sidecar(tmp_path)
    (p / "plan-graph.yaml").write_text("edges: []\n", encoding="utf-8")
    assert set(pa.file_hashes(p)) == {"plan.md", "phase-1.md", "plan-graph.yaml"}
