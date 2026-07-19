"""Read-only partner (ccs) job lens (phase 6, twin of test_lens_gemini.py).

Aggregates the partner job registry (harness/state/partner/jobs.jsonl) by
purpose+provider: counts, TOTAL cost (partner has real cost, unlike gemini),
pass/degrade, latency percentiles. Fail-soft: a torn JSONL line is skipped,
never a crash. Registry-wired so hs:insights surfaces it.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
import lens_partner as lp  # noqa: E402


def _rec(job_id, purpose, provider, status, ts, cost=None):
    r = {"job_id": job_id, "purpose": purpose, "provider": provider,
         "status": status, "ts": ts}
    if cost is not None:
        r["cost"] = cost
    return r


def test_aggregate_by_provider_with_cost():
    recs = [
        _rec("a", "review", "minimax", "done", "2026-07-11T10:00:01+00:00", cost=0.12),
        _rec("b", "review", "minimax", "done", "2026-07-11T10:00:02+00:00", cost=0.08),
        _rec("c", "research", "ds", "done", "2026-07-11T10:00:03+00:00", cost=0.05),
        _rec("d", "review", "minimax", "degraded", "2026-07-11T10:00:04+00:00"),
    ]
    agg = lp._aggregate(recs)
    assert agg["total_jobs"] == 4
    groups = {(g["purpose"], g["provider"]): g for g in agg["by_group"]}
    review_minimax = groups[("review", "minimax")]
    assert review_minimax["count"] == 3
    assert round(review_minimax["cost"], 2) == 0.20  # 0.12 + 0.08, degraded has no cost
    assert review_minimax["passed"] == 2
    assert review_minimax["degraded"] == 1
    research_ds = groups[("research", "ds")]
    assert research_ds["count"] == 1
    assert research_ds["cost"] == 0.05


def test_registered_in_analyze_telemetry():
    import analyze_telemetry as at
    assert "partner" in at.LENS_REGISTRY
    assert "partner" in at.OVERVIEW_ORDER


def test_bad_line_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    d = tmp_path / "partner"
    d.mkdir()
    (d / "jobs.jsonl").write_text(
        json.dumps(_rec("a", "review", "minimax", "done",
                        "2026-07-11T10:00:01+00:00", cost=0.1)) + "\n"
        + "{ this is not json\n"
        + json.dumps(_rec("b", "review", "minimax", "done",
                          "2026-07-11T10:00:02+00:00", cost=0.1)) + "\n",
        encoding="utf-8")
    agg = lp.gather(days=3650)
    assert agg["total_jobs"] == 2
