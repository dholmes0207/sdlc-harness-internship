"""Tests for lens_docs_build — docs-build outcome lens (read-only)."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lens_docs_build as lens  # noqa: E402


def _seed(tmp_path, rows):
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    lines = [json.dumps({"ts": now, **r}) for r in rows]
    (tel / "docs-build.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_no_sink_renders_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    agg = lens.gather(days=30)
    assert agg["total_builds"] == 0
    assert "No docs-build runs" in lens.render(agg)


def test_counts_ok_and_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [
        {"phase": "showcase", "outcome": "ok", "pages": 13, "diagrams": 11, "md_sourced": 6},
        {"phase": "showcase", "outcome": "failed", "stage": "validate", "errors": 2},
        {"phase": "showcase", "outcome": "ok", "pages": 14, "diagrams": 11, "md_sourced": 6},
    ])
    agg = lens.gather(days=30)
    assert agg["total_builds"] == 3
    assert agg["ok"] == 2
    assert agg["failed"] == 1
    assert agg["latest_ok"]["pages"] == 14  # most recent ok wins
    assert agg["fail_stages"][0]["stage"] == "validate"


def test_render_surfaces_latest_and_failstage(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [
        {"outcome": "ok", "pages": 14, "diagrams": 11, "md_sourced": 6},
        {"outcome": "failed", "stage": "validate"},
    ])
    out = lens.render(lens.gather(days=30))
    assert "latest successful build" in out
    assert "14 page" in out
    assert "validate" in out


def test_bad_lines_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    (tel / "docs-build.jsonl").write_text(
        "not json\n" + json.dumps({"ts": now, "outcome": "ok", "pages": 1}) + "\n",
        encoding="utf-8")
    agg = lens.gather(days=30)
    assert agg["total_builds"] == 1
