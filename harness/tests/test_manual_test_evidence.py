"""test_manual_test_evidence.py — manual-test admissibility (anchored vs claimed).

The anti-fabrication floor: a telemetry-anchored output proves
"a real command ran and this is its real output" — it kills pure hallucination —
but it does NOT prove the command tested the right thing. So:
  - evidence_tier `claimed` (agent-written) is BELOW the floor → never hard-admissible.
  - `anchored` requires the cited anchor id to actually exist in the anchor
    telemetry; a fabricated id that is not in the sink is REJECTED.
  - even a real anchored output is hard-admissible ONLY with a valid human
    charter co-sign (a rostered reviewer, distinct from the author). Anchored
    without a co-sign stays SOFT. No "forgery-proof" overclaim.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import manual_test as mt  # noqa: E402

_MT_SCRIPT = _SCRIPTS / "manual_test.py"


def _run_cli(tmp_path, *args):
    """Run manual_test.py as a real subprocess (dogfood the actual CLI). Telemetry
    is off under pytest, so clear the disable markers the way the hook test does."""
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_TELEMETRY_DISABLED", None)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_USER"] = "tester"
    return subprocess.run([sys.executable, str(_MT_SCRIPT), *args],
                          capture_output=True, text=True, env=env)


def _cli_sink(tmp_path):
    p = tmp_path / "state" / "telemetry" / "manual-test-anchor.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.is_file() else []


def _seed_anchor(tmp_path, command):
    """Write one real anchor record to the sink the gate cross-checks."""
    sink = tmp_path / "telemetry" / "manual-test-anchor.jsonl"
    sink.parent.mkdir(parents=True, exist_ok=True)
    aid = mt.anchor_id_for(command)
    import json
    from datetime import datetime, timezone
    sink.write_text(json.dumps({
        "anchor_id": aid, "cmd_hash": aid, "actor": "user:tester",
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }) + "\n", encoding="utf-8")
    return aid


def _team(tmp_path):
    p = tmp_path / "team.yaml"
    p.write_text('reviewers: ["user:lead@x.com"]\nallow_self_review: false\n',
                 encoding="utf-8")
    return p


# --- tier resolution -----------------------------------------------------------
def test_claimed_is_below_floor(tmp_path):
    tier, _ = mt.admissibility({"evidence_tier": "claimed"}, root=tmp_path)
    assert tier == "claimed"


def test_anchored_with_real_id_is_anchored(tmp_path):
    aid = _seed_anchor(tmp_path, "curl -s http://localhost/health")
    tier, _ = mt.admissibility(
        {"evidence_tier": "anchored", "anchor_id": aid}, root=tmp_path)
    assert tier == "anchored"


def test_anchored_with_fabricated_id_is_rejected(tmp_path):
    _seed_anchor(tmp_path, "curl -s http://localhost/health")
    tier, reason = mt.admissibility(
        {"evidence_tier": "anchored", "anchor_id": "deadbeefdeadbeef"},
        root=tmp_path)
    assert tier == "rejected"
    assert "anchor" in reason.lower()


# --- hard admissibility (needs cosign) -----------------------------------------
def test_anchored_without_cosign_is_not_hard(tmp_path):
    aid = _seed_anchor(tmp_path, "curl -s http://localhost/health")
    ok, reason = mt.hard_admissible(
        {"evidence_tier": "anchored", "anchor_id": aid, "actor": "user:dev"},
        root=tmp_path, team_path=_team(tmp_path))
    assert ok is False
    assert "co-sign" in reason.lower() or "cosign" in reason.lower()


def test_anchored_with_valid_cosign_is_hard(tmp_path):
    aid = _seed_anchor(tmp_path, "curl -s http://localhost/health")
    ok, _ = mt.hard_admissible(
        {"evidence_tier": "anchored", "anchor_id": aid, "actor": "user:dev",
         "charter_cosign": "user:lead@x.com"},
        root=tmp_path, team_path=_team(tmp_path))
    assert ok is True


def test_claimed_is_never_hard(tmp_path):
    ok, _ = mt.hard_admissible(
        {"evidence_tier": "claimed", "actor": "user:dev",
         "charter_cosign": "user:lead@x.com"},
        root=tmp_path, team_path=_team(tmp_path))
    assert ok is False


def test_self_cosign_is_not_hard(tmp_path):
    # A self co-sign (cosign == author) is NOT hard-admissible: the schema
    # promises a reviewer DISTINCT from the author, so a hard gate needs a
    # co-sign that is not the actor. (This tightens the earlier personal-first
    # stance to match the schema's distinct-reviewer promise.)
    aid = _seed_anchor(tmp_path, "curl -s http://localhost/health")
    ok, reason = mt.hard_admissible(
        {"evidence_tier": "anchored", "anchor_id": aid,
         "actor": "user:lead@x.com", "charter_cosign": "user:lead@x.com"},
        root=tmp_path, team_path=_team(tmp_path))
    assert ok is False
    assert "distinct" in reason.lower() or "actor" in reason.lower()


def test_distinct_cosign_is_hard(tmp_path):
    aid = _seed_anchor(tmp_path, "curl -s http://localhost/health")
    ok, _ = mt.hard_admissible(
        {"evidence_tier": "anchored", "anchor_id": aid,
         "actor": "user:dev", "charter_cosign": "user:lead@x.com"},
        root=tmp_path, team_path=_team(tmp_path))
    assert ok is True


def test_cosign_without_actor_is_not_hard(tmp_path):
    # Omitting `actor` must not dodge the distinctness check — with no author to
    # compare against, distinctness cannot be established, so it stays soft.
    aid = _seed_anchor(tmp_path, "curl -s http://localhost/health")
    ok, _ = mt.hard_admissible(
        {"evidence_tier": "anchored", "anchor_id": aid,
         "charter_cosign": "user:lead@x.com"},  # no actor field
        root=tmp_path, team_path=_team(tmp_path))
    assert ok is False


# --- quadrant: the sink FILE is absent (not just a missing id) -----------------
def test_anchored_with_no_sink_file_is_rejected(tmp_path):
    # No anchor sink exists at all -> an anchored citation is REJECTED, never
    # softened to claimed. This is the floor the portability work must not erode.
    tier, reason = mt.admissibility(
        {"evidence_tier": "anchored", "anchor_id": "deadbeefdeadbeef"},
        root=tmp_path)  # tmp_path has no telemetry/ sink
    assert tier == "rejected"


def test_anchored_no_sink_is_not_hard(tmp_path):
    ok, _ = mt.hard_admissible(
        {"evidence_tier": "anchored", "anchor_id": "deadbeefdeadbeef",
         "actor": "user:dev", "charter_cosign": "user:lead@x.com"},
        root=tmp_path)
    assert ok is False


# --- verify_portable: transport-integrity only, no tier, outside the gate ------
def test_verify_portable_matches_cited_cmd_hash():
    cmd = "printf PASS"
    check = {"cmd": cmd, "cmd_hash": mt.anchor_id_for(cmd),
             "output_hash": hashlib.sha256(b"PASS").hexdigest()[:16]}
    ok, _ = mt.verify_portable(check)
    assert ok is True  # transport intact...
    ok2, _ = mt.verify_portable(check, observed_output="PASS")
    assert ok2 is True  # ...and the reviewer's own re-run output matches


def test_verify_portable_detects_transit_alteration():
    check = {"cmd": "printf PASS", "cmd_hash": "0000000000000000"}
    ok, reason = mt.verify_portable(check)
    assert ok is False


def test_verify_portable_wrong_rerun_output_fails():
    cmd = "printf PASS"
    check = {"cmd": cmd, "cmd_hash": mt.anchor_id_for(cmd),
             "output_hash": hashlib.sha256(b"PASS").hexdigest()[:16]}
    ok, _ = mt.verify_portable(check, observed_output="TAMPERED")
    assert ok is False


def test_verify_portable_usable_on_the_documented_check_shape():
    # The manual-check shape the SKILL documents (cmd + cmd_hash + output_hash)
    # must actually be usable by verify_portable — else the docs tell a reviewer
    # to run a helper that always returns "nothing to check".
    cmd = "curl -s http://localhost:8080/health"
    check = {
        "name": "manual", "format": "manual", "status": "PASS",
        "evidence_tier": "anchored", "anchor_id": mt.anchor_id_for(cmd),
        "cmd": cmd, "cmd_hash": mt.anchor_id_for(cmd),
        "output_hash": hashlib.sha256(b"OK").hexdigest()[:16],
        "actor": "user:dev", "charter_cosign": "user:lead",
    }
    ok, _ = mt.verify_portable(check)
    assert ok is True
    ok2, _ = mt.verify_portable(check, observed_output="OK")
    assert ok2 is True


def test_verify_portable_is_not_wired_into_the_gate():
    # portability must NOT grant a tier — the DoD gate never calls it.
    gate_src = (_SCRIPTS / "artifact_check.py").read_text(encoding="utf-8")
    assert "verify_portable" not in gate_src


# --- agent-memory (compounding lessons) ----------------------------------------
def test_record_lesson_appends_feedback_and_project(tmp_path):
    assert mt.record_lesson("feedback", "env DB_URL must be set first",
                            root=tmp_path, actor="user:t") is True
    assert mt.record_lesson("project", {"bug": "expired token resets",
                                       "repro": "POST /reset w/ stale token"},
                            root=tmp_path, actor="user:t") is True
    import json as _json
    fb = (tmp_path / "manual-tester" / "feedback.jsonl").read_text().splitlines()
    pj = (tmp_path / "manual-tester" / "project.jsonl").read_text().splitlines()
    assert _json.loads(fb[0])["payload"] == "env DB_URL must be set first"
    assert _json.loads(pj[0])["actor"] == "user:t" and "ts" in _json.loads(pj[0])


def test_record_lesson_rejects_unknown_kind(tmp_path):
    assert mt.record_lesson("evidence", "nope", root=tmp_path) is False


# --- committed discovery ledger (a marker for humans, not an evidence tier) -----
def test_append_ledger_creates_file_with_header_and_row(tmp_path):
    p = mt.append_ledger(tmp_path, {"charter": "login smoke", "story": "PRD-AUTH-E1-S1",
                                    "anchor_ids": ["abc123"], "cosign": "user:lead"},
                         actor="user:t")
    assert p.name == "manual-test-log.md"
    assert p.parent == tmp_path  # under plans/<plan>/, NOT the state dir
    text = p.read_text(encoding="utf-8")
    assert "| when | actor | charter | story | anchor_ids | cosign |" in text
    assert "login smoke" in text and "PRD-AUTH-E1-S1" in text and "user:lead" in text
    assert "user:t" in text


def test_append_ledger_appends_without_touching_prior_rows(tmp_path):
    p = mt.append_ledger(tmp_path, {"charter": "first"}, actor="user:t")
    before = p.read_text(encoding="utf-8")
    mt.append_ledger(tmp_path, {"charter": "second"}, actor="user:t")
    after = p.read_text(encoding="utf-8")
    assert after.startswith(before)          # nothing above was rewritten
    assert after.count("| when |") == 1      # header written exactly once
    assert "first" in after and "second" in after
    assert after.rstrip().endswith("|")


def test_append_ledger_stamps_ts(tmp_path):
    p = mt.append_ledger(tmp_path, {"charter": "c"}, actor="user:t")
    # the first data row carries an ISO-ish ts in the `when` column
    rows = [ln for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| ") and "when" not in ln and "---" not in ln]
    assert rows and "2026" in rows[0]


def test_append_ledger_escapes_markdown_in_charter(tmp_path):
    # A pipe or newline in the charter must not break the table structure.
    p = mt.append_ledger(tmp_path, {"charter": "a | b\nc", "story": "S"}, actor="user:t")
    lines = p.read_text(encoding="utf-8").splitlines()
    data_rows = [ln for ln in lines if ln.startswith("| ") and "when" not in ln and "---" not in ln]
    assert len(data_rows) == 1                      # the newline did not spawn a 2nd row
    assert "\\|" in data_rows[0]                     # the literal pipe was escaped
    # unescaped pipes = the 7 real cell separators (6 cells); the escaped one
    # does not count as a separator.
    assert data_rows[0].replace("\\|", "").count("|") == 7


# --- session marker gate (arm anchoring without the env var) -------------------
def test_arm_and_session_armed_roundtrip(tmp_path):
    assert mt.session_armed(tmp_path, "sess-A") is False
    mt.arm_session(tmp_path, "sess-A")
    assert mt.session_armed(tmp_path, "sess-A") is True


def test_marker_is_session_scoped(tmp_path):
    mt.arm_session(tmp_path, "sess-A")
    assert mt.session_armed(tmp_path, "sess-B") is False


def test_marker_lives_under_state_not_telemetry(tmp_path):
    p = mt.arm_session(tmp_path, "sess-A")
    assert "manual-test-session" in str(p)
    assert "telemetry" not in str(p)


def test_arm_session_idempotent_append_only(tmp_path):
    p1 = mt.arm_session(tmp_path, "sess-A")
    before = p1.read_bytes()
    p2 = mt.arm_session(tmp_path, "sess-A")  # re-arm is a no-op write
    assert p1 == p2
    assert p2.read_bytes() == before


def test_marker_session_id_sanitized_no_traversal(tmp_path):
    # A hostile session id cannot escape the marker dir into a parent path.
    p = mt.session_marker_path(tmp_path, "../../evil")
    assert p.parent == (tmp_path / "manual-test-session")
    mt.arm_session(tmp_path, "../../evil")
    assert mt.session_armed(tmp_path, "../../evil") is True


def test_arm_session_record_carries_actor_and_ts(tmp_path):
    import json as _json
    p = mt.arm_session(tmp_path, "sess-A", actor="user:t")
    rec = _json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert rec["actor"] == "user:t"
    assert "ts" in rec


def test_arm_session_refuses_degenerate_id(tmp_path):
    # A falsy or all-sanitized-away session id must NOT create the shared "_"
    # bucket that would arm every non-session Bash command indefinitely.
    for bad in (None, "", "/", "..", "___"):
        assert mt.arm_session(tmp_path, bad) is None
    marker_dir = tmp_path / "manual-test-session"
    assert not marker_dir.exists() or not any(marker_dir.iterdir())


def test_session_armed_false_for_degenerate_id(tmp_path):
    for bad in (None, "", "/", "___"):
        assert mt.session_armed(tmp_path, bad) is False


def test_build_anchor_source_defaults_to_hook():
    assert mt.build_anchor("echo hi")["source"] == "hook"


def test_build_anchor_source_explicit_cli():
    assert mt.build_anchor("echo hi", source="cli")["source"] == "cli"


# --- explicit CLI --anchor (self-exec, subagent-safe, no agent-supplied output) -
def test_cli_anchor_self_execs_and_hashes_real_output(tmp_path):
    proc = _run_cli(tmp_path, "--anchor", "--cmd", "printf hi")
    assert proc.returncode == 0, proc.stderr
    recs = _cli_sink(tmp_path)
    assert recs, "an anchor must be written"
    rec = recs[0]
    assert rec["anchor_id"] == mt.anchor_id_for("printf hi")
    assert rec["source"] == "cli"
    assert rec["output_hash"] == hashlib.sha256(b"hi").hexdigest()[:16]
    assert mt.anchor_exists(mt.anchor_id_for("printf hi"), root=tmp_path / "state")


def test_cli_anchor_has_no_agent_supplied_output_flags(tmp_path):
    # The agent has NO way to feed the output — the naive --output-file /
    # --output-stdin flags must not exist, so argparse rejects them.
    assert _run_cli(tmp_path, "--anchor", "--cmd", "printf hi",
                    "--output-file", "/tmp/x").returncode != 0
    assert _run_cli(tmp_path, "--anchor", "--cmd", "printf hi",
                    "--output-stdin").returncode != 0


def test_cli_anchor_binary_output_fails_soft_not_crash(tmp_path):
    # A probe that emits non-UTF-8 bytes DID run -> still anchor its (sanitized)
    # output; never a raw traceback (the decode of the captured bytes must not
    # escape as UnicodeDecodeError).
    proc = _run_cli(tmp_path, "--anchor", "--cmd", r"printf '\xff\xfe'")
    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    recs = _cli_sink(tmp_path)
    assert recs and recs[0]["source"] == "cli"
    assert "output_hash" in recs[0]


def test_cli_output_hash_is_real_capture_not_declared(tmp_path):
    # The hash reflects the REAL captured output, never the command string or a
    # value the agent expects: a command printing SURPRISE hashes SURPRISE.
    _run_cli(tmp_path, "--anchor", "--cmd", "printf SURPRISE")
    rec = _cli_sink(tmp_path)[0]
    assert rec["output_hash"] == hashlib.sha256(b"SURPRISE").hexdigest()[:16]
    assert rec["output_hash"] != hashlib.sha256(b"printf SURPRISE").hexdigest()[:16]


def test_cli_anchor_requires_cmd(tmp_path):
    proc = _run_cli(tmp_path, "--anchor")
    assert proc.returncode != 0
    assert _cli_sink(tmp_path) == []


def test_cli_nonzero_exit_still_anchored_with_exit_code(tmp_path):
    # A command that runs and exits non-zero DID run — anchor it, record the code.
    proc = _run_cli(tmp_path, "--anchor", "--cmd", "sh -c 'printf out; exit 3'")
    assert proc.returncode == 0, proc.stderr
    rec = _cli_sink(tmp_path)[0]
    assert rec["source"] == "cli"
    assert rec["exit_code"] == 3
    assert rec["output_hash"] == hashlib.sha256(b"out").hexdigest()[:16]


def test_cli_anchor_runs_without_session_env(tmp_path):
    # The whole point: --anchor works with NO HARNESS_MANUAL_TEST_SESSION set
    # (subagent-safe). _run_cli never sets it.
    proc = _run_cli(tmp_path, "--anchor", "--cmd", "printf ok")
    assert proc.returncode == 0
    assert _cli_sink(tmp_path)
