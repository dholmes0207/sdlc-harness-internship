"""test_lens_observations.py — the judgment-signal lens (read-only, honesty-gated).

This lens surfaces the closed-vocab signals skills emit at their end-of-work checkpoint.
Its honesty gate is the whole point: if the BASELINE (skill invocations, read from the
existing invocations.jsonl) is sparse, the lens must NOT read silence as "nothing to
improve" — it says the corpus is too thin to judge. The baseline reuses invocations.jsonl
so no new hook is needed.
"""
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lens_observations as lens  # noqa: E402


def _seed(tmp_path, obs_rows, inv_count):
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(days=1)).isoformat()
    obs = [json.dumps({"ts": ts, "skill": s, "signal": sig, "payload": "p", "actor": "user:x"})
           for s, sig in obs_rows]
    (tel / "observations.jsonl").write_text(("\n".join(obs) + "\n") if obs else "", encoding="utf-8")
    inv = [json.dumps({"ts": ts, "skill": "hs:plan", "via": "PreToolUse:Skill"})
           for _ in range(inv_count)]
    (tel / "invocations.jsonl").write_text(("\n".join(inv) + "\n") if inv else "", encoding="utf-8")


def test_lens_observations_registered():
    import analyze_telemetry as at
    assert "observations" in at.LENS_REGISTRY
    modname, _ = at.LENS_REGISTRY["observations"]
    mod = importlib.import_module(modname)
    assert hasattr(mod, "gather") and hasattr(mod, "render")


def test_lens_honesty_gate_sparse(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [("hs:plan", "thin-evidence")], inv_count=2)  # baseline 2 < MIN
    agg = lens.gather(days=30)
    assert agg["gated"] is True
    out = lens.render(agg).lower()
    assert "sparse" in out or "insufficient" in out or "too thin" in out


def test_lens_baseline_reads_invocations(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [("hs:plan", "thin-evidence"), ("hs:cook", "gate-repeat-block")], inv_count=8)
    agg = lens.gather(days=30)
    assert agg["baseline_invocations"] == 8  # reads the existing invocations.jsonl
    assert agg["total_observations"] == 2
    assert agg["gated"] is False


def test_lens_under_emission_not_clean(tmp_path, monkeypatch):
    # baseline sufficient but ZERO judgment signals => the lens names the under-emission,
    # it does NOT read silence as "nothing to improve" (the exact failure it exists to fix).
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [], inv_count=8)
    agg = lens.gather(days=30)
    assert agg["total_observations"] == 0
    assert agg["gated"] is False
    out = lens.render(agg).lower()
    assert "0" in out and ("emit" in out or "signal" in out)
