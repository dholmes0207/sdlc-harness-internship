"""test_stamp_artifact_hook.py — PostToolUse provenance-stamp hook.

The hook stamps a freshly-written markdown artifact under plans/ in place,
reading the live release identity. It is telemetry-class: fail-open and scoped
to plans/**/*.md — a non-plans path, a non-markdown file, or a missing target
is a silent no-op, never an error.
"""
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _d in (str(_SCRIPTS), str(_HOOKS)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import stamp_artifact  # noqa: E402


def _seed_root(tmp_path):
    """A minimal harness tree: a manifest (for kit_digest) + plans/."""
    h = tmp_path / "harness"
    h.mkdir(parents=True)
    (h / "manifest.json").write_text(
        json.dumps({"files": {"harness/a.py": "0" * 64}}, indent=2,
                   sort_keys=True) + "\n", encoding="utf-8")
    (tmp_path / "plans" / "reports").mkdir(parents=True)


def _payload(file_path):
    return {"tool_name": "Write", "tool_input": {"file_path": str(file_path)}}


def test_stamps_md_under_plans(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    _seed_root(tmp_path)
    f = tmp_path / "plans" / "reports" / "x-report.md"
    f.write_text("# Report\n\nbody\n", encoding="utf-8")
    stamp_artifact.core(_payload(f))
    out = f.read_text(encoding="utf-8")
    assert out.startswith("---\n")
    assert "harness_version:" in out
    assert "harness_kit_digest:" in out
    assert out.endswith("# Report\n\nbody\n")


def test_hook_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    _seed_root(tmp_path)
    f = tmp_path / "plans" / "reports" / "x-report.md"
    f.write_text("# Report\n\nbody\n", encoding="utf-8")
    stamp_artifact.core(_payload(f))
    once = f.read_text(encoding="utf-8")
    stamp_artifact.core(_payload(f))
    assert f.read_text(encoding="utf-8") == once


def test_ignores_non_plans_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    _seed_root(tmp_path)
    (tmp_path / "docs").mkdir()
    f = tmp_path / "docs" / "guide.md"
    f.write_text("# Guide\n", encoding="utf-8")
    stamp_artifact.core(_payload(f))
    assert f.read_text(encoding="utf-8") == "# Guide\n"


def test_ignores_non_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    _seed_root(tmp_path)
    f = tmp_path / "plans" / "data.txt"
    f.write_text("raw\n", encoding="utf-8")
    stamp_artifact.core(_payload(f))
    assert f.read_text(encoding="utf-8") == "raw\n"


def test_fail_open_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    _seed_root(tmp_path)
    ghost = tmp_path / "plans" / "reports" / "ghost.md"
    stamp_artifact.core(_payload(ghost))  # must not raise


def test_fail_open_no_file_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    _seed_root(tmp_path)
    stamp_artifact.core({"tool_name": "Write", "tool_input": {}})  # must not raise
