"""test_lens_workflow_chains.py — declared-vs-actual skill chains lens.

Declared side (harness/data/skill-chains.yaml, env HARNESS_SKILL_CHAINS
override) fails LOUD when the file is missing or malformed — a packaging bug
must never read as "zero declared chains". Actual side folds telemetry
invocations.jsonl per session and is fail-soft on bad lines. Pure gather →
render-agnostic dict, read-only.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lens_workflow_chains as lens  # noqa: E402


def _mk_skill(sdir: Path, dirname: str, name: str):
    d = sdir / dirname
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: %s\n---\n" % name, encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Skills catalog + chains fixture + telemetry sink, all under tmp."""
    sdir = tmp_path / "skills"
    for d, n in (("hs-plan", "hs:plan"), ("hs-cook", "hs:cook"),
                 ("hs-test", "hs:test")):
        _mk_skill(sdir, d, n)

    chains = tmp_path / "skill-chains.yaml"
    chains.write_text(
        "chains:\n"
        "  - [hs:plan, hs:cook]\n"
        "  - [hs:cook, hs:test]\n"
        "  - [hs:test, hs:cook]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARNESS_SKILL_CHAINS", str(chains))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    return {"skills": sdir, "chains": chains, "tmp": tmp_path}


def _write_invocations(tmp_path, rows):
    p = tmp_path / "state" / "telemetry" / "invocations.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with open(p, "w", encoding="utf-8") as fh:
        for sess, skill in rows:
            fh.write(json.dumps({"session": sess, "skill": skill, "ts": now}) + "\n")


class TestDeclared:
    def test_declared_chains_normalized_to_dir_slugs(self, env):
        out = lens.gather(skills_dir=env["skills"])
        assert "hs-plan → hs-cook" in out["declared_chains"]
        assert len(out["declared_chains"]) == 3

    def test_missing_file_fails_loud(self, env, monkeypatch):
        monkeypatch.setenv("HARNESS_SKILL_CHAINS",
                           str(env["tmp"] / "gone.yaml"))
        with pytest.raises(FileNotFoundError):
            lens.gather(skills_dir=env["skills"])

    def test_malformed_chain_entry_fails_loud(self, env):
        env["chains"].write_text("chains:\n  - just-a-string\n",
                                 encoding="utf-8")
        with pytest.raises(ValueError):
            lens.gather(skills_dir=env["skills"])

    def test_absent_chains_key_means_deliberately_none(self, env):
        env["chains"].write_text("{}\n", encoding="utf-8")
        out = lens.gather(skills_dir=env["skills"])
        assert out["declared_chains"] == []


class TestActual:
    def test_lens_parses_ts_written_by_production_writer(self, env, monkeypatch):
        # Round-trip through the REAL writer: telemetry_paths.append_event
        # enriches ts; the lens must parse that exact format (not just the
        # isoformat() shape the other fixtures fabricate). A parse failure
        # would silently disable the days-cutoff for every real record.
        import telemetry_paths as tp
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
        monkeypatch.setenv("HARNESS_USER", "alice")
        monkeypatch.setattr(tp, "_actor_cache", None)
        tp.append_event("invocations.jsonl", {"session": "s1", "skill": "hs:plan"})
        tp.append_event("invocations.jsonl", {"session": "s1", "skill": "hs:cook"})

        # session s1 shards to invocations-s1.jsonl (P2)
        raw_ts = json.loads(
            (env["tmp"] / "state" / "telemetry" / "invocations-s1.jsonl")
            .read_text(encoding="utf-8").splitlines()[0])["ts"]
        assert lens._parse_ts(raw_ts) is not None  # the writer format parses

        out = lens.gather(days=1, skills_dir=env["skills"])
        assert out["sessions_analyzed"] == 1  # records survive the days cutoff
        chains = {c["chain"] for c in out["common_chains"]}
        assert "hs-plan → hs-cook" in chains

    def test_actual_chains_fold_per_session_in_ts_order(self, env):
        _write_invocations(env["tmp"], [
            ("s1", "hs:plan"), ("s1", "hs:cook"),
            ("s2", "hs:cook"), ("s2", "hs:test"),
        ])
        out = lens.gather(skills_dir=env["skills"])
        assert out["sessions_analyzed"] == 2
        chains = {c["chain"] for c in out["common_chains"]}
        assert "hs-plan → hs-cook" in chains

    def test_deviation_not_in_declared_is_flagged(self, env):
        _write_invocations(env["tmp"], [
            ("s1", "hs:test"), ("s1", "hs:plan"),  # reversed, undeclared
        ])
        out = lens.gather(skills_dir=env["skills"])
        assert any(d["chain"] == "hs-test → hs-plan" for d in out["deviations"])

    def test_undeclared_chains_scored_as_formalization_candidates(self, env):
        # A recurring undeclared workflow is a candidate to formalize (declare
        # as a chain or distil into a skill). Score rewards BOTH how often it
        # recurs AND how many steps it collapses (frequency x steps) — the part
        # of the cowork score formula the telemetry actually supports; effort
        # saved and risk are not measurable from chain data, so they are not
        # invented into the score.
        _write_invocations(env["tmp"], [
            ("s1", "hs:test"), ("s1", "hs:plan"), ("s1", "hs:cook"),
            ("s2", "hs:test"), ("s2", "hs:plan"), ("s2", "hs:cook"),
            ("s3", "hs:test"), ("s3", "hs:plan"), ("s3", "hs:cook"),
            ("s4", "hs:cook"), ("s4", "hs:plan"),  # undeclared, but only once
        ])
        out = lens.gather(skills_dir=env["skills"])
        cands = out["candidates"]
        assert cands, "expected at least one formalization candidate"
        top = cands[0]
        assert top["chain"] == "hs-test → hs-plan → hs-cook"
        assert top["count"] == 3 and top["steps"] == 3
        assert top["score"] == 9            # frequency 3 x steps 3
        assert top["score"] > cands[-1]["score"]  # rarer/shorter ranks below

    def test_records_with_unparseable_ts_excluded_under_days_cutoff(self, env):
        # A record whose ts is missing/garbage cannot be placed inside the
        # --days window, so with a cutoff active it must be EXCLUDED — never
        # silently counted (None < cutoff is False, which would let it slip
        # through). Only the in-window record survives.
        p = env["tmp"] / "state" / "telemetry" / "invocations.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"session": "s-none", "skill": "hs:plan",
                                 "ts": None}) + "\n")
            fh.write(json.dumps({"session": "s-none", "skill": "hs:cook",
                                 "ts": "not-a-timestamp"}) + "\n")
            fh.write(json.dumps({"session": "s-ok", "skill": "hs:plan",
                                 "ts": now}) + "\n")
            fh.write(json.dumps({"session": "s-ok", "skill": "hs:cook",
                                 "ts": now}) + "\n")
        out = lens.gather(days=1, skills_dir=env["skills"])
        assert out["sessions_analyzed"] == 1  # only s-ok counts
        chains = {c["chain"] for c in out["common_chains"]}
        assert "hs-plan → hs-cook" in chains

    def test_no_telemetry_file_fail_soft_zero_sessions(self, env):
        out = lens.gather(skills_dir=env["skills"])
        assert out["sessions_analyzed"] == 0
        assert out["gated"] is True  # low-volume gate suppresses advice

    def test_skips_non_object_jsonl_line(self, env):
        # a parseable-but-non-object line must be SKIPPED, not blank the lens
        p = env["tmp"] / "state" / "telemetry" / "invocations.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[1,2,3]\n")
            fh.write(json.dumps({"session": "s1", "skill": "hs:plan", "ts": now}) + "\n")
            fh.write(json.dumps({"session": "s1", "skill": "hs:cook", "ts": now}) + "\n")
        out = lens.gather(skills_dir=env["skills"])  # must not raise
        assert out["sessions_analyzed"] == 1
        assert any(c["chain"] == "hs-plan → hs-cook" for c in out["common_chains"])

    def test_parse_ts_naive_is_normalized_to_aware(self):
        # A no-offset ts parses to a naive datetime; left naive it raised
        # TypeError against the aware --days cutoff. It must come back aware.
        dt = lens._parse_ts("2026-06-15T10:00:00")
        assert dt is not None and dt.tzinfo is not None

    def test_naive_ts_line_does_not_crash_lens(self, env):
        # A malformed-but-parseable tz-naive ts must be placed in the window
        # (fail-soft), never crash the whole lens with a TypeError.
        p = env["tmp"] / "state" / "telemetry" / "invocations.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()  # no offset
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"session": "s1", "skill": "hs:plan", "ts": naive}) + "\n")
            fh.write(json.dumps({"session": "s1", "skill": "hs:cook", "ts": naive}) + "\n")
        out = lens.gather(days=1, skills_dir=env["skills"])  # must NOT raise
        chains = {c["chain"] for c in out["common_chains"]}
        assert "hs-plan → hs-cook" in chains

    def test_negative_top_is_clamped_not_negative_sliced(self, env):
        # --top -2 reached gather as top=-2: most_common(-2) returns [] and
        # deviations[:-2] dropped the tail instead of limiting. top must clamp
        # to a positive limit so output is a real top-N, not empty/tail-dropped.
        _write_invocations(env["tmp"], [
            ("s1", "hs:plan"), ("s1", "hs:cook"),
            ("s2", "hs:cook"), ("s2", "hs:test"),
        ])
        out = lens.gather(days=30, top=-2, skills_dir=env["skills"])
        assert len(out["common_chains"]) >= 1
