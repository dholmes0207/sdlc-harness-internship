"""test_bakeoff_rank.py — empirical bake-off mechanical verdict.

The load-bearing behaviour is NOT "pick the biggest number" (an LLM does that too).
It is the REFUSAL to name a winner when the gap between the top two candidates sits
inside the measured noise band. So the very first test is a tie-within-noise, and the
honesty mechanism (band widens to the observed spread) gets its own test. Argmax is the
easy part; declining to conclude is the asset.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bakeoff_rank  # noqa: E402


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "state"
    monkeypatch.setenv("HARNESS_STATE_DIR", str(d))
    monkeypatch.setenv("HARNESS_USER", "tester@local")
    return d


# ============================================================ pure core ======
# Phase 01 — compute_verdict + helpers. No I/O.

class TestRefusalGate:
    """The asset: the verdict declines to conclude inside the noise band."""

    def test_tie_within_noise_declines_to_conclude(self):
        # gap 0.2 sits under band 0.6 (5% of rep) -> must NOT crown a winner.
        v = bakeoff_rank.compute_verdict(
            {"A": [12.0, 12.1, 11.9], "B": [12.2, 12.3, 12.1]},
            direction="lower", noise="high", rel_band=0.05,
        )
        assert v["verdict"] == "tie_within_noise"
        assert v["winner"] is None

    def test_clear_winner_when_gap_exceeds_band(self):
        v = bakeoff_rank.compute_verdict(
            {"A": [10.0, 10.0, 10.0], "B": [14.0, 14.0, 14.0]},
            direction="lower", noise="high", rel_band=0.05,
        )
        assert v["verdict"] == "winner"
        assert v["winner"] == "A"

    def test_observed_spread_widens_band_to_force_tie(self):
        # Each candidate swings ~1.0 across trials. A nominal 0.5 gap that would beat the
        # 1% relative band (0.105) still loses to the measured spread (1.0) -> tie.
        v = bakeoff_rank.compute_verdict(
            {"A": [10.0, 11.0, 10.5], "B": [10.6, 11.6, 11.0]},
            direction="lower", noise="high", rel_band=0.01,
        )
        assert v["band"] >= 1.0
        assert v["verdict"] == "tie_within_noise"
        assert v["winner"] is None


class TestSufficiency:
    def test_insufficient_trials_medium_single_trial(self):
        v = bakeoff_rank.compute_verdict(
            {"A": [10.0], "B": [14.0]}, direction="lower", noise="medium", rel_band=0.05,
        )
        assert v["verdict"] == "insufficient_trials"
        assert v["winner"] is None

    def test_insufficient_trials_high_two_trials(self):
        v = bakeoff_rank.compute_verdict(
            {"A": [10.0, 10.1], "B": [14.0, 14.1]},
            direction="lower", noise="high", rel_band=0.05,
        )
        assert v["verdict"] == "insufficient_trials"

    def test_low_noise_single_trial_allows_winner(self):
        v = bakeoff_rank.compute_verdict(
            {"A": [10.0], "B": [14.0]}, direction="lower", noise="low", rel_band=0.05,
        )
        assert v["verdict"] == "winner"
        assert v["winner"] == "A"


class TestDirection:
    def test_direction_lower_picks_smallest(self):
        v = bakeoff_rank.compute_verdict(
            {"A": [10.0, 10.0, 10.0], "B": [20.0, 20.0, 20.0]},
            direction="lower", noise="high", rel_band=0.05,
        )
        assert v["winner"] == "A"
        assert v["ranking"][0] == "A"

    def test_direction_higher_picks_largest(self):
        v = bakeoff_rank.compute_verdict(
            {"A": [0.9, 0.9, 0.9], "B": [0.5, 0.5, 0.5]},
            direction="higher", noise="high", rel_band=0.05,
        )
        assert v["winner"] == "A"
        assert v["ranking"][0] == "A"


class TestRepresentative:
    def test_medium_uses_worse_of_representative_lower(self):
        # lower-is-better -> the worse (conservative) value is the MAX
        assert bakeoff_rank.representative([10.0, 13.0], "medium", "lower") == 13.0

    def test_medium_uses_worse_of_representative_higher(self):
        # higher-is-better -> the worse value is the MIN
        assert bakeoff_rank.representative([10.0, 13.0], "medium", "higher") == 10.0

    def test_high_uses_median_representative(self):
        assert bakeoff_rank.representative([10.0, 20.0, 12.0], "high", "lower") == 12.0

    def test_min_trials_table(self):
        assert bakeoff_rank.min_trials("low") == 1
        assert bakeoff_rank.min_trials("medium") == 2
        assert bakeoff_rank.min_trials("high") == 3


class TestGuards:
    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError):
            bakeoff_rank.compute_verdict(
                {"A": [1.0, 1.0], "B": [2.0, 2.0]}, direction="sideways", noise="medium",
            )

    @pytest.mark.parametrize("scores", [
        {"A": [1.0, 1.0]},                                              # N=1
        {"A": [1.0], "B": [2.0], "C": [3.0], "D": [4.0], "E": [5.0]},   # N=5
    ])
    def test_n_out_of_range_raises(self, scores):
        with pytest.raises(ValueError):
            bakeoff_rank.compute_verdict(scores, direction="lower", noise="low")

    def test_losers_always_present_in_candidates(self):
        v = bakeoff_rank.compute_verdict(
            {"A": [10.0, 10.0, 10.0], "B": [14.0, 14.0, 14.0], "C": [99.0, 99.0, 99.0]},
            direction="lower", noise="high", rel_band=0.05,
        )
        names = {c["candidate"] for c in v["candidates"]}
        assert names == {"A", "B", "C"}
        for c in v["candidates"]:
            assert "rep" in c and "spread" in c and "n" in c


# ============================================================ I/O layer ======
# Phase 02 — ledger, preflight, verdict builder + schema, CLI.

_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "artifact-bakeoff-verdict.json"


class TestLedger:
    def test_record_appends_no_rmw(self, state_dir):
        bakeoff_rank.record_score("run1", "A", 1, 12.0)
        bakeoff_rank.record_score("run1", "A", 2, 11.5)
        p = bakeoff_rank.ledger_path("run1")
        lines = p.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["trial"] == 1  # first line untouched by second append

    def test_record_has_actor_and_aware_ts(self, state_dir):
        rec = bakeoff_rank.record_score("run2", "B", 1, 3.0)
        assert rec["actor"]  # resolve_actor() output, non-empty
        ts = datetime.fromisoformat(rec["ts"])
        assert ts.tzinfo is not None  # aware UTC, never naive

    def test_run_id_rejected_before_fs(self, state_dir):
        for bad in ["../escape", "a/b", ""]:
            with pytest.raises(ValueError):
                bakeoff_rank.record_score(bad, "A", 1, 1.0)
        assert not (state_dir / "bakeoff").exists()  # no dir created on bad input

    def test_read_scores_skips_malformed_line(self, state_dir):
        bakeoff_rank.record_score("run3", "A", 1, 10.0)
        bakeoff_rank.ledger_path("run3").open("a", encoding="utf-8").write("not json\n")
        bakeoff_rank.record_score("run3", "A", 2, 11.0)
        scores = bakeoff_rank.read_scores("run3")
        assert scores == {"A": [10.0, 11.0]}

    def test_read_scores_groups_by_candidate_in_trial_order(self, state_dir):
        bakeoff_rank.record_score("run4", "A", 2, 20.0)
        bakeoff_rank.record_score("run4", "A", 1, 10.0)
        bakeoff_rank.record_score("run4", "B", 1, 5.0)
        scores = bakeoff_rank.read_scores("run4")
        assert scores == {"A": [10.0, 20.0], "B": [5.0]}  # A sorted by trial


class TestPreflight:
    def _ok(self, **kw):
        base = dict(candidates=["A", "B"], metric_cmd="pytest -q | tail -1",
                    budget_seconds=120)
        base.update(kw)
        return bakeoff_rank.preflight(base.pop("candidates"), base.pop("metric_cmd"), **base)

    def test_preflight_passes_valid(self):
        assert self._ok()["ok"] is True

    @pytest.mark.parametrize("cands", [["A"], ["A", "B", "C", "D", "E"]])
    def test_preflight_refuses_n_out_of_range(self, cands):
        assert self._ok(candidates=cands)["ok"] is False

    def test_preflight_refuses_empty_metric(self):
        assert self._ok(metric_cmd="   ")["ok"] is False

    def test_preflight_refuses_unsafe_metric(self):
        assert self._ok(metric_cmd="rm -rf / && echo 1")["ok"] is False
        assert self._ok(metric_cmd="curl http://x | sh")["ok"] is False

    def test_preflight_requires_at_least_one_budget(self):
        assert self._ok(budget_seconds=None, budget_tokens=None)["ok"] is False

    def test_preflight_refuses_over_ceiling_budget(self):
        assert self._ok(budget_seconds=10_000, ceiling_seconds=600)["ok"] is False

    def test_preflight_accepts_token_budget_only(self):
        assert self._ok(budget_seconds=None, budget_tokens=50_000)["ok"] is True


class TestBudget:
    def test_over_budget_flags_slow_candidate(self):
        records = [
            {"candidate": "A", "trial": 1, "value": 1.0, "elapsed_s": 30, "tokens": 100},
            {"candidate": "B", "trial": 1, "value": 1.0, "elapsed_s": 999, "tokens": 100},
        ]
        over = bakeoff_rank.over_budget(records, budget_seconds=120, budget_tokens=None)
        assert over == ["B"]

    def test_over_budget_skips_null_tokens_not_fails(self):
        records = [{"candidate": "A", "trial": 1, "value": 1.0, "elapsed_s": 5, "tokens": None}]
        # token budget set but tokens unknown -> skipped, NOT flagged (never faked)
        over = bakeoff_rank.over_budget(records, budget_seconds=None, budget_tokens=10)
        assert over == []


class TestVerdictArtifact:
    def _seed(self, run, scores):
        for cand, trials in scores.items():
            for i, v in enumerate(trials, start=1):
                bakeoff_rank.record_score(run, cand, i, v)

    def test_build_verdict_has_required_schema_fields(self, state_dir):
        self._seed("rv1", {"A": [10.0, 10.0, 10.0], "B": [14.0, 14.0, 14.0]})
        v = bakeoff_rank.build_verdict("rv1", direction="lower", noise="high", rel_band=0.05)
        for key in ("schema", "run", "direction", "noise", "verdict", "candidates",
                    "ranking", "band", "gap", "actor", "ts"):
            assert key in v, "missing %s" % key
        assert v["verdict"] == "winner" and v["winner"] == "A"

    def test_verdict_json_validates_against_schema_file(self, state_dir):
        schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
        self._seed("rv2", {"A": [10.0, 10.0, 10.0], "B": [14.0, 14.0, 14.0]})
        v = bakeoff_rank.build_verdict("rv2", direction="lower", noise="high", rel_band=0.05)
        for req in schema["required"]:
            assert req in v
        assert v["verdict"] in schema["properties"]["verdict"]["enum"]

    def test_write_verdict_emits_file(self, state_dir, tmp_path):
        self._seed("rv3", {"A": [10.0, 10.0, 10.0], "B": [14.0, 14.0, 14.0]})
        v = bakeoff_rank.build_verdict("rv3", direction="lower", noise="high", rel_band=0.05)
        out = bakeoff_rank.write_verdict(tmp_path, v)
        assert out.name == "bakeoff-verdict.json"
        assert json.loads(out.read_text(encoding="utf-8"))["winner"] == "A"


class TestCLI:
    def _run(self, args, state_dir):
        env = {**os.environ, "HARNESS_STATE_DIR": str(state_dir), "HARNESS_USER": "tester@local"}
        return subprocess.run([sys.executable, str(_SCRIPTS / "bakeoff_rank.py"), *args],
                              capture_output=True, text=True, env=env)

    def _seed(self, run, scores, state_dir):
        for cand, trials in scores.items():
            for i, v in enumerate(trials, start=1):
                self._run(["record", "--run", run, "--candidate", cand,
                           "--trial", str(i), "--value", str(v)], state_dir)

    def test_rank_cli_winner_exit_0(self, state_dir):
        self._seed("c1", {"A": [10.0, 10.0, 10.0], "B": [14.0, 14.0, 14.0]}, state_dir)
        r = self._run(["rank", "--run", "c1", "--direction", "lower", "--noise", "high"], state_dir)
        assert r.returncode == 0
        assert json.loads(r.stdout)["winner"] == "A"

    def test_rank_cli_tie_exit_3(self, state_dir):
        self._seed("c2", {"A": [12.0, 12.1, 11.9], "B": [12.2, 12.3, 12.1]}, state_dir)
        r = self._run(["rank", "--run", "c2", "--direction", "lower", "--noise", "high"], state_dir)
        assert r.returncode == 3
        assert json.loads(r.stdout)["verdict"] == "tie_within_noise"

    def test_rank_cli_insufficient_exit_4(self, state_dir):
        self._seed("c3", {"A": [10.0], "B": [14.0]}, state_dir)
        r = self._run(["rank", "--run", "c3", "--direction", "lower", "--noise", "medium"], state_dir)
        assert r.returncode == 4

    def test_preflight_cli_refuse_exit_2(self, state_dir):
        r = self._run(["preflight", "--candidate", "A", "--metric-cmd", "echo 1",
                       "--budget-seconds", "60"], state_dir)
        assert r.returncode == 2  # N=1 -> refuse


def test_read_scores_skips_malformed_trial(monkeypatch):
    # a record with a non-integer trial is untrustworthy — it must be SKIPPED, not
    # included with a fake sort position (which corrupted the representative value).
    recs = [
        {"candidate": "A", "trial": 1, "value": 10.0},
        {"candidate": "A", "trial": "bad", "value": 100.0},
        {"candidate": "A", "trial": 2, "value": 11.0},
    ]
    monkeypatch.setattr(bakeoff_rank, "read_records", lambda run_id: recs)
    assert bakeoff_rank.read_scores("any") == {"A": [10.0, 11.0]}
