"""Unit tests for the per-repo review-dismissals store.

The store records a reviewer's dismissal of a finding so a later review can SHOW
("this was dismissed before, reason X") without ever auto-suppressing it. It is
append-only JSONL, every record carries actor+ts, and its fingerprint is stable
across a reformat (no line number) yet distinguishes the same rule firing twice in
one file (it hashes the normalized snippet).
"""
import inspect
import json
import multiprocessing
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import dismissals_store as ds  # noqa: E402
import validate_artifacts as va  # noqa: E402

_SCHEMA = json.loads(
    (_ROOT / "harness" / "schemas" / "review-dismissal.json").read_text(encoding="utf-8")
)


def _concurrent_writer(args):
    """Top-level (picklable) worker for the cross-process append test."""
    root, i, big = args
    ds.append({"fingerprint": "c%d" % i, "verdict": "dismissed",
               "reason": "r", "file": "f", "code_evidence": big}, root=root)


# ---------- fingerprint ----------

def test_fingerprint_stable_across_reformat():
    # same (file, rule, snippet) with different leading/trailing indentation
    # must hash identically — line numbers and boundary whitespace do not count.
    a = ds.fingerprint("src/a.py", "no-eval", "    x = eval(y)\n")
    b = ds.fingerprint("src/a.py", "no-eval", "x = eval(y)")
    assert a == b


def test_fingerprint_distinguishes_same_rule_twice():
    # same file + rule, different snippet → different fingerprint (disambiguates
    # one rule firing at two sites without using line numbers).
    a = ds.fingerprint("src/a.py", "no-eval", "x = eval(y)")
    b = ds.fingerprint("src/a.py", "no-eval", "z = eval(w)")
    assert a != b


def test_fingerprint_internal_whitespace_distinct():
    # normalize strips only boundary whitespace, NOT internal — two snippets that
    # differ only in internal spacing must stay distinct (no false collision).
    a = ds.fingerprint("src/a.py", "r", "a = b+c")
    b = ds.fingerprint("src/a.py", "r", "a = b + c")
    assert a != b


def test_fingerprint_no_cross_field_collision():
    # a literal '|' in a field must not shift the join boundary and collide a
    # distinct (file, rule) pair — each field is hashed before joining.
    assert ds.fingerprint("a|no-eval", "x", "s") != ds.fingerprint("a", "no-eval|x", "s")


# ---------- append / lookup ----------

def _store(tmp_path: Path) -> Path:
    return tmp_path / "docs" / "review" / "dismissals.jsonl"


def test_append_carries_actor_ts(tmp_path):
    rec = ds.append({"fingerprint": "ff", "verdict": "dismissed",
                     "reason": "noise", "file": "src/a.py"}, root=tmp_path)
    assert rec["actor"]
    assert rec["ts"]
    assert rec["schema_version"]
    on_disk = json.loads(_store(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert on_disk["actor"] == rec["actor"]


def test_append_only_no_rmw(tmp_path):
    ds.append({"fingerprint": "a", "verdict": "dismissed", "reason": "x",
               "file": "f"}, root=tmp_path)
    ds.append({"fingerprint": "b", "verdict": "dismissed", "reason": "y",
               "file": "f"}, root=tmp_path)
    lines = _store(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # second append did not erase the first


def test_lookup_returns_all_matches(tmp_path):
    ds.append({"fingerprint": "dup", "verdict": "dismissed", "reason": "1",
               "file": "f"}, root=tmp_path)
    ds.append({"fingerprint": "dup", "verdict": "dismissed", "reason": "2",
               "file": "f"}, root=tmp_path)
    got = ds.lookup("dup", root=tmp_path)
    assert len(got) == 2  # both surfaced, none hidden


def test_lookup_skips_unparseable(tmp_path):
    ds.append({"fingerprint": "ok", "verdict": "dismissed", "reason": "r",
               "file": "f"}, root=tmp_path)
    # simulate an old spliced/garbage line
    with _store(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write('{"fingerprint": "ok", BROKEN\n')
    got = ds.lookup("ok", root=tmp_path)  # must not raise
    assert len(got) == 1  # the one valid record survives the skip


def test_append_large_record_concurrent(tmp_path):
    # >PIPE_BUF records written by separate PROCESSES must each stay on their own
    # line (flock holds the append atomic). Threads would NOT exercise this — the
    # GIL serializes a single fh.write() — so use real processes, the actual hazard
    # (two reviewer processes on one repo). Without the lock these splice and the
    # JSON parse below blows up.
    n = 40
    big = "X" * 5000
    args = [(str(tmp_path), i, big) for i in range(n)]
    ctx = multiprocessing.get_context("spawn")  # spawn re-imports — no fork-state surprises
    with ctx.Pool(8) as pool:
        pool.map(_concurrent_writer, args)
    lines = _store(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == n
    for ln in lines:
        json.loads(ln)  # every line is whole JSON, no splice


# ---------- schema (R7) ----------

def test_schema_version_present(tmp_path):
    rec = ds.append({"fingerprint": "s", "verdict": "dismissed", "reason": "r",
                     "file": "f"}, root=tmp_path)
    assert "schema_version" in rec
    # schema rejects a record missing schema_version
    missing = dict(rec)
    missing.pop("schema_version")
    findings = va.validate(_SCHEMA, missing)
    assert any("schema_version" in f for f in findings)


def test_schema_accepts_clean_record(tmp_path):
    rec = ds.append({"fingerprint": "s", "verdict": "dismissed", "reason": "r",
                     "file": "f"}, root=tmp_path)
    assert va.validate(_SCHEMA, rec) == []


def test_schema_rejects_bad_verdict():
    bad = {"schema_version": ds.SCHEMA_VERSION, "fingerprint": "x",
           "verdict": "maybe", "reason": "r", "file": "f",
           "actor": "user:x", "ts": "2026-01-01T00:00:00+00:00"}
    findings = va.validate(_SCHEMA, bad)
    assert any("verdict" in f for f in findings)


# ---------- git-visibility ----------

@pytest.mark.dev_repo
def test_store_path_git_visible():
    # in the dev repo, docs/ is NOT gitignored → the store path is git-visible.
    import subprocess
    rc = subprocess.run(
        ["git", "check-ignore", "docs/review/dismissals.jsonl"],
        cwd=_ROOT, capture_output=True, text=True).returncode
    assert rc == 1  # 1 == not ignored


def test_store_visible_in_clean_repo(tmp_path):
    # a fresh repo that did NOT inherit the dev .gitignore: shipping the
    # docs/review/.gitkeep keeps the store dir tracked and the file visible.
    import subprocess

    def _git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path,
                              capture_output=True, text=True)

    _git("init", "-q")
    (tmp_path / "docs" / "review").mkdir(parents=True)
    (tmp_path / "docs" / "review" / ".gitkeep").write_text("", encoding="utf-8")
    _git("add", "docs/review/.gitkeep")
    # the actual ship mechanism: .gitkeep is TRACKED, so the store dir survives a clone
    tracked = _git("ls-files", "docs/review/.gitkeep").stdout.strip()
    assert tracked == "docs/review/.gitkeep", "shipped .gitkeep must be tracked"
    # and the store path itself is not gitignored in a repo without a docs/ ignore rule
    rc = _git("check-ignore", "docs/review/dismissals.jsonl").returncode
    assert rc == 1  # not ignored in a clean repo → store survives a ship/clone
