"""test_claims.py — lease-based claim files over empirically-validated primitives.

The claims store is a deliberate exception to append-only JSONL:
race-free coordination needs O_CREAT|O_EXCL acquire and rename-consumed
reclaim, which a log cannot give. Claim files are immutable post-create and
only ever RENAMED (.reclaim/, .done/, *.quarantine) — never deleted:
delete-then-recreate reclaim is the proven split-brain primitive.

Race tests are barrier-synced (no sleeps) and run 20 consecutive rounds so a
flaky primitive cannot hide behind a lucky schedule.
"""
import json
import multiprocessing
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import claims  # noqa: E402

# fork is the fast, inheritance-based default on Linux/macOS, but it does not
# exist on Windows — `get_context("fork")` there raises at import and takes the
# whole suite down at collection. Fall back to spawn (the workers are top-level
# and set their own env, so they are spawn-safe) so the race tests still run.
_START_METHOD = ("fork" if "fork" in multiprocessing.get_all_start_methods()
                 else "spawn")
_CTX = multiprocessing.get_context(_START_METHOD)


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "state"
    monkeypatch.setenv("HARNESS_STATE_DIR", str(d))
    monkeypatch.setenv("HARNESS_USER", "tester@local")
    return d


def _put_claim(state_dir, task_id, claim_id="seedclaim", expires_in_s=-3600,
               content=None):
    """Drop a claim file directly (test fixture state, not the lib API)."""
    d = state_dir / "claims"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (task_id + ".claim")
    if content is not None:
        p.write_bytes(content)
        return p
    now = datetime.now(timezone.utc)
    rec = {
        "task_id": task_id,
        "actor": "user:seed@local",
        "ts": now.isoformat(),
        "claim_id": claim_id,
        "expires_ts": (now + timedelta(seconds=expires_in_s)).isoformat(),
        "lease_s": abs(expires_in_s),
    }
    p.write_text(json.dumps(rec), encoding="utf-8")
    return p


def _trace_text(state_dir):
    trace = state_dir / "trace"
    if not trace.is_dir():
        return ""
    return "".join(p.read_text(encoding="utf-8")
                   for p in sorted(trace.glob("trace-*.jsonl")))


# ---------- task_id sanitization ----------

class TestTaskIdSanitization:
    @pytest.mark.parametrize("bad", [
        "../../x", "a/b", "", ".", "..", "a\x00b", "a b", "a\nb", "ụnicode",
    ])
    def test_rejects_bad_task_id_before_touching_fs(self, state_dir, bad):
        with pytest.raises(claims.ClaimInputError) as exc:
            claims.acquire(bad, lease_s=60)
        assert "task_id" in str(exc.value)
        assert not (state_dir / "claims").exists()

    def test_status_and_release_and_reclaim_also_reject(self, state_dir):
        for fn in (lambda: claims.status("a/b"),
                   lambda: claims.release("a/b", "x"),
                   lambda: claims.reclaim("a/b")):
            with pytest.raises(claims.ClaimInputError):
                fn()
        assert not (state_dir / "claims").exists()

    def test_accepts_normal_ids(self, state_dir):
        for ok in ("T-1", "issue.42", "a_b-c.D"):
            assert claims.acquire(ok, lease_s=60)["ok"]


# ---------- acquire ----------

class TestAcquire:
    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_rejects_non_positive_lease(self, state_dir, bad):
        # a non-positive lease makes expires_ts <= now -> the claim is born STALE and
        # any reclaim wins instantly. The lease resolver already rejects
        # this; the direct/CLI path must too.
        with pytest.raises(claims.ClaimInputError) as exc:
            claims.acquire("T-neg", lease_s=bad)
        assert "lease_s" in str(exc.value)
        assert not (state_dir / "claims" / "T-neg.claim").exists()

    def test_acquire_creates_immutable_claim_with_lease_in_content(self, state_dir):
        r = claims.acquire("T-1", lease_s=120)
        assert r["ok"] and r["state"] == "CLAIMED"
        p = state_dir / "claims" / "T-1.claim"
        data = json.loads(p.read_text(encoding="utf-8"))
        for field in ("task_id", "actor", "ts", "claim_id", "expires_ts", "lease_s"):
            assert field in data, field
        assert data["task_id"] == "T-1"
        assert data["lease_s"] == 120
        assert data["actor"].startswith("user:tester@local")
        exp = datetime.fromisoformat(data["expires_ts"])
        ts = datetime.fromisoformat(data["ts"])
        assert (exp - ts).total_seconds() == pytest.approx(120, abs=1)

    def test_loser_gets_existing_claim_back(self, state_dir):
        first = claims.acquire("T-2", lease_s=60)
        second = claims.acquire("T-2", lease_s=60)
        assert second["ok"] is False
        assert second["reason"] == "exists"
        assert second["existing"]["claim_id"] == first["claim"]["claim_id"]

    def test_lease_defaults_and_env_seam(self, state_dir, monkeypatch):
        # No explicit lease: the module constant supplies the default (14400);
        # HARNESS_CLAIM_LEASE_S overrides it (personal-first: no roster config source).
        monkeypatch.delenv("HARNESS_CLAIM_LEASE_S", raising=False)
        assert claims.acquire("T-3")["claim"]["lease_s"] == 14400
        monkeypatch.setenv("HARNESS_CLAIM_LEASE_S", "60")
        assert claims.acquire("T-3b")["claim"]["lease_s"] == 60

    def test_acquire_race_exactly_one_winner_20_rounds(self, state_dir):
        rounds, nproc = 20, 8
        for rnd in range(rounds):
            task = "race-%d" % rnd
            barrier = _CTX.Barrier(nproc)
            q = _CTX.Queue()
            procs = [_CTX.Process(target=_acquire_worker,
                                  args=(str(state_dir), task, barrier, q, i))
                     for i in range(nproc)]
            for p in procs:
                p.start()
            results = [q.get(timeout=30) for _ in range(nproc)]
            for p in procs:
                p.join(timeout=30)
            winners = [idx for idx, ok in results if ok]
            assert len(winners) == 1, "round %d winners=%s" % (rnd, winners)


def _acquire_worker(state_dir, task_id, barrier, q, idx):
    os.environ["HARNESS_STATE_DIR"] = state_dir
    os.environ["HARNESS_USER"] = "racer-%d" % idx
    barrier.wait()
    r = claims.acquire(task_id, lease_s=60)
    q.put((idx, r["ok"]))


# ---------- status / staleness ----------

class TestStatus:
    def test_unclaimed_when_no_file(self, state_dir):
        r = claims.status("T-none")
        assert r["state"] == "UNCLAIMED" and r["claim"] is None

    def test_fresh_claim_is_claimed(self, state_dir):
        claims.acquire("T-4", lease_s=3600)
        assert claims.status("T-4")["state"] == "CLAIMED"

    def test_expired_lease_is_stale(self, state_dir):
        _put_claim(state_dir, "T-5", expires_in_s=-10)
        r = claims.status("T-5")
        assert r["state"] == "STALE"
        assert r["claim"]["claim_id"] == "seedclaim"

    def test_mtime_tamper_does_not_change_verdict_when_content_parses(self, state_dir):
        # Lease lives in CONTENT; mtime is same-uid writable and clobbered by
        # cp/sync, so it must not flip a parseable claim either way.
        p = _put_claim(state_dir, "T-6", expires_in_s=3600)  # fresh by content
        past = datetime.now(timezone.utc).timestamp() - 10 * 3600
        os.utime(p, (past, past))
        assert claims.status("T-6")["state"] == "CLAIMED"

        p2 = _put_claim(state_dir, "T-7", expires_in_s=-3600)  # stale by content
        now = datetime.now(timezone.utc).timestamp()
        os.utime(p2, (now, now))
        assert claims.status("T-7")["state"] == "STALE"

    def test_unparsable_content_treated_claimed(self, state_dir):
        _put_claim(state_dir, "T-8", content=b"\x00not json at all")
        r = claims.status("T-8")
        assert r["state"] == "CLAIMED"
        assert r["claim"] is None
        assert "unparsable" in r["note"]

    def test_unparsable_content_goes_stale_on_mtime_grace(self, state_dir):
        p = _put_claim(state_dir, "T-9", content=b"garbage")
        past = datetime.now(timezone.utc).timestamp() - (claims.FALLBACK_GRACE_S + 600)
        os.utime(p, (past, past))
        assert claims.status("T-9")["state"] == "STALE"


# ---------- reclaim (4-step, rename-consumed) ----------

class TestReclaim:
    def test_reclaim_fresh_claim_refused(self, state_dir):
        _put_claim(state_dir, "T-10", expires_in_s=3600)
        r = claims.reclaim("T-10")
        assert r["ok"] is False and r["reason"] == "not_stale"

    def test_reclaim_stale_wins_moves_tombstone_and_reacquires(self, state_dir):
        _put_claim(state_dir, "T-11", claim_id="oldclaim", expires_in_s=-60)
        r = claims.reclaim("T-11", lease_s=60)
        assert r["ok"] and r["state"] == "CLAIMED"
        assert r["claim"]["claim_id"] != "oldclaim"
        tombs = list((state_dir / "claims" / ".reclaim").glob("T-11.oldclaim.*.json"))
        assert len(tombs) == 1
        assert "claim_reclaimed" in _trace_text(state_dir)

    def test_reclaim_unclaimed_loses_cleanly(self, state_dir):
        r = claims.reclaim("T-12")
        assert r["ok"] is False and r["reason"] == "unclaimed"

    def test_reclaim_toctou_restores_fresh_claim(self, state_dir):
        # Between staleness read and rename, the claim is legitimately
        # released and a FRESH claim appears at the same path: the reclaimer
        # must detect the claim_id change and put the fresh claim back.
        p = _put_claim(state_dir, "T-13", claim_id="oldclaim", expires_in_s=-60)

        def swap_in_fresh():
            os.rename(p, p.with_name("T-13.released"))
            _put_claim(state_dir, "T-13", claim_id="freshclaim", expires_in_s=3600)

        r = claims.reclaim("T-13", _after_read=swap_in_fresh)
        assert r["ok"] is False and r["reason"] == "fresh_claim_restored"
        restored = json.loads(p.read_text(encoding="utf-8"))
        assert restored["claim_id"] == "freshclaim"

    def test_reclaim_toctou_quarantines_when_path_reoccupied(self, state_dir):
        # Same theft window, but by the time the reclaimer tries to put the
        # stolen fresh claim back, the path is occupied again: the stolen
        # file must be quarantined (visible + traced), never dropped, and the
        # occupying claim must be left intact.
        p = _put_claim(state_dir, "T-14", claim_id="oldclaim", expires_in_s=-60)

        def swap_in_fresh():
            os.rename(p, p.with_name("T-14.released"))
            _put_claim(state_dir, "T-14", claim_id="freshclaim", expires_in_s=3600)

        def reoccupy():
            _put_claim(state_dir, "T-14", claim_id="thirdclaim", expires_in_s=3600)

        r = claims.reclaim("T-14", _after_read=swap_in_fresh, _after_rename=reoccupy)
        assert r["ok"] is False and r["reason"] == "quarantined"
        quarantined = list((state_dir / "claims" / ".reclaim").glob("*.quarantine"))
        assert len(quarantined) == 1
        assert json.loads(quarantined[0].read_text(encoding="utf-8"))["claim_id"] == "freshclaim"
        survivor = json.loads(p.read_text(encoding="utf-8"))
        assert survivor["claim_id"] == "thirdclaim"
        assert "claim_quarantined" in _trace_text(state_dir)

    def test_reclaim_race_exactly_one_winner_20_rounds(self, state_dir):
        rounds, nproc = 20, 8
        for rnd in range(rounds):
            task = "stale-%d" % rnd
            _put_claim(state_dir, task, claim_id="dead-%d" % rnd, expires_in_s=-60)
            barrier = _CTX.Barrier(nproc)
            q = _CTX.Queue()
            procs = [_CTX.Process(target=_reclaim_worker,
                                  args=(str(state_dir), task, barrier, q, i))
                     for i in range(nproc)]
            for p in procs:
                p.start()
            results = [q.get(timeout=30) for _ in range(nproc)]
            for p in procs:
                p.join(timeout=30)
            winners = [idx for idx, ok, _ in results if ok]
            losses = {reason for _, ok, reason in results if not ok}
            assert len(winners) == 1, "round %d winners=%s" % (rnd, winners)
            assert losses <= {"lost_race", "unclaimed"}, losses


def _reclaim_worker(state_dir, task_id, barrier, q, idx):
    os.environ["HARNESS_STATE_DIR"] = state_dir
    os.environ["HARNESS_USER"] = "reclaimer-%d" % idx
    barrier.wait()
    r = claims.reclaim(task_id, reacquire=False)
    q.put((idx, r["ok"], r.get("reason")))


# ---------- release ----------

class TestRelease:
    def test_release_wrong_claim_id_refused(self, state_dir):
        claims.acquire("T-15", lease_s=60)
        r = claims.release("T-15", "not-my-claim")
        assert r["ok"] is False and r["reason"] == "claim_id_mismatch"
        assert (state_dir / "claims" / "T-15.claim").exists()

    def test_release_moves_claim_to_done(self, state_dir):
        a = claims.acquire("T-16", lease_s=60)
        cid = a["claim"]["claim_id"]
        r = claims.release("T-16", cid)
        assert r["ok"] and r["state"] == "RELEASED"
        assert not (state_dir / "claims" / "T-16.claim").exists()
        assert (state_dir / "claims" / ".done" / ("T-16.%s.json" % cid)).exists()
        assert claims.status("T-16")["state"] == "UNCLAIMED"
        assert "claim_released" in _trace_text(state_dir)

    def test_release_unclaimed_refused(self, state_dir):
        r = claims.release("T-17", "whatever")
        assert r["ok"] is False and r["reason"] == "unclaimed"

    def test_release_does_not_clobber_reclaimed_fresh_claim(self, state_dir):
        # Between release()'s claim read and its rename, the original claim
        # legitimately expires, is reclaimed, and a FRESH claim takes the
        # same path. release(A) must re-verify just before the rename and
        # refuse — never tombstone the new holder's valid claim.
        a = claims.acquire("T-18", lease_s=60)
        cid_a = a["claim"]["claim_id"]

        def reclaim_to_b():
            # Simulate the interleave: A's slot is replaced by a fresh B.
            p = state_dir / "claims" / "T-18.claim"
            os.rename(p, p.with_name("T-18.tombstoned"))
            _put_claim(state_dir, "T-18", claim_id="claim-B", expires_in_s=3600)

        r = claims.release("T-18", cid_a, _after_read=reclaim_to_b)
        assert r["ok"] is False and r["reason"] == "claim_id_mismatch"
        survivor = json.loads(
            (state_dir / "claims" / "T-18.claim").read_text(encoding="utf-8"))
        assert survivor["claim_id"] == "claim-B"  # B not clobbered
        assert not (state_dir / "claims" / ".done").exists()


# ---------- team.yaml loader ----------

class TestCLI:
    def test_cli_acquire_writes_claim_and_trace_event(self, tmp_path):
        state = tmp_path / "state"
        env = dict(os.environ)
        env["HARNESS_STATE_DIR"] = str(state)
        env["HARNESS_USER"] = "cli-user@local"
        out = subprocess.run(
            [sys.executable, str(_SCRIPTS / "claims.py"), "acquire", "T-cli",
             "--lease-s", "60"],
            capture_output=True, text=True, env=env, timeout=30)
        assert out.returncode == 0, out.stderr
        result = json.loads(out.stdout)
        assert result["ok"] and result["claim"]["task_id"] == "T-cli"
        assert (state / "claims" / "T-cli.claim").exists()
        assert "claim_acquired" in _trace_text(state)

    def test_cli_rejects_bad_task_id_nonzero_exit(self, tmp_path):
        env = dict(os.environ)
        env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
        out = subprocess.run(
            [sys.executable, str(_SCRIPTS / "claims.py"), "acquire", "../evil"],
            capture_output=True, text=True, env=env, timeout=30)
        assert out.returncode != 0
        result = json.loads(out.stdout)
        assert result["ok"] is False and "task_id" in result["error"]


def test_claim_file_is_created_owner_only(state_dir):
    # Multi-user host: a group/world-writable lease file lets another local
    # user clobber the O_EXCL winner after it wins. The lease file must be 0o600.
    import stat as _stat
    res = claims.acquire("mode-task", lease_s=60)
    assert res["ok"]
    p = state_dir / "claims" / "mode-task.claim"
    mode = _stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600, oct(mode)
