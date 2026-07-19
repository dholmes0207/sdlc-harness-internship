"""test_perf_dashboard.py — JSONL self-timing -> static HTML dashboard.

perf_dashboard reads the diag core_timing stream and renders p50/p95 per hook,
spawn/day, and a slowest-first ranking into ONE self-contained HTML file (inline
CSS, no CDN, no DuckDB required — a pure-python percentile fallback keeps it
dependency-free and self-contained).
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import perf_dashboard as pd  # noqa: E402


def _diag(tmp_path, records):
    d = tmp_path / "diag"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "diag.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


def _timing(hook, ms, ts="2026-07-10T10:00:00+00:00"):
    return {"ts": ts, "level": "INFO", "event": "core_timing", "hook": hook,
            "class": "compliance", "elapsed_ms": ms, "status": "ok"}


class TestMetrics:
    def test_p50_p95_per_hook(self, tmp_path):
        recs = [_timing("gate_stage", m) for m in (10, 20, 30, 40, 100)]
        recs += [_timing("secret_scan", m) for m in (1, 2, 3)]
        agg = pd.aggregate(pd.read_timing(_diag(tmp_path, recs)))
        gs = agg["gate_stage"]
        assert gs["count"] == 5
        assert gs["p50"] == 30            # median of 10,20,30,40,100
        assert gs["p95"] >= 40            # tail near the top
        assert agg["secret_scan"]["p50"] == 2

    def test_spawn_per_day(self, tmp_path):
        recs = [_timing("h", 1, ts="2026-07-10T01:00:00+00:00") for _ in range(3)]
        recs += [_timing("h", 1, ts="2026-07-11T01:00:00+00:00") for _ in range(2)]
        per_day = pd.spawn_per_day(pd.read_timing(_diag(tmp_path, recs)))
        assert per_day["2026-07-10"] == 3 and per_day["2026-07-11"] == 2

    def test_slowest_ranked_by_p95(self, tmp_path):
        recs = [_timing("slow", 100), _timing("slow", 200)]
        recs += [_timing("fast", 1), _timing("fast", 2)]
        agg = pd.aggregate(pd.read_timing(_diag(tmp_path, recs)))
        ranked = pd.slowest(agg)
        assert ranked[0][0] == "slow"     # highest p95 first
        assert ranked[-1][0] == "fast"


class TestRender:
    def test_html_self_contained_no_dep(self, tmp_path):
        recs = [_timing("gate_stage", m) for m in (5, 15, 25)]
        html = pd.render_html(pd.aggregate(pd.read_timing(_diag(tmp_path, recs))),
                              pd.spawn_per_day(pd.read_timing(_diag(tmp_path, recs))))
        assert "<html" in html.lower() and "gate_stage" in html
        assert "http://" not in html and "https://" not in html  # no CDN, self-contained
        assert "p95" in html.lower()

    def test_empty_input_graceful(self, tmp_path):
        (tmp_path / "diag").mkdir(parents=True)
        html = pd.render_html(pd.aggregate([]), pd.spawn_per_day([]))
        assert "no data" in html.lower()

    def test_main_writes_html(self, tmp_path, monkeypatch):
        recs = [_timing("gate_stage", 12)]
        _diag(tmp_path, recs)
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        out = tmp_path / "dash.html"
        rc = pd.main(["--out", str(out)])
        assert rc == 0 and out.is_file()
        assert "gate_stage" in out.read_text(encoding="utf-8")
