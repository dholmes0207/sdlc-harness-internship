"""Contract for the two-tier eval-memory store (eval_memory.py).

L4 lock (plan.md 0.1/0.5): tier-1 per-repo lessons/incidents/decisions live at
`<target>/evals/_memory/<type>.jsonl` (tracked in the target repo); tier-2
standards live per-machine under a resolved harness state home. These tests
pin: the append/recall roundtrip per tier, mask-before-write at the BYTE
level (not just the in-memory string), tolerant-read on a corrupt line,
`--limit` required, append-only (no rewrite of prior bytes), the self-target
fence, `--auto-hash` agreeing with `eval_config.py hash`, concurrent-writer
safety via flock, unicode roundtrip, and the FORK B cross-platform home
resolution (path-string only -- native locking is OS-territory and not
exercised here, see eval_memory.py's own docstring).
"""

import json
import subprocess
import sys
import textwrap

import importlib.util

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts"
_SCRIPT = _SCRIPTS_DIR / "eval_memory.py"
_EVAL_CONFIG_SCRIPT = _SCRIPTS_DIR / "eval_config.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_memory", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(args, input_text=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT)] + args,
        capture_output=True, text=True, input=input_text)


def _sample_card(**overrides):
    """Minimal valid `contract`-strategy card -- enough for eval_config.py to
    write eval_config.json + eval_config.sha256, which --auto-hash reads."""
    card = {
        "schema_version": "1.0",
        "domain": "cv_extraction",
        "strategy": "contract",
        "surface": "extraction",
        "production_module": "src/cv_extraction.py",
        "production_entry": "extract",
        "mirror_lang": "python",
        "mirror_invoke": None,
        "forge": "github",
        "threshold": 70,
        "p0_rules": [
            {"rule": "name must be non-null", "source": "code:src/cv_extraction.py:42",
             "target_axis": "accuracy", "violation_value": None},
        ],
        "dimensions": {"accuracy": 60, "completeness": 40},
        "primary_dimension": "accuracy",
        "judge_model": None,
        "pipeline_model": None,
        "domain_config": {"normalizers": {"phone": "phone_vi"}, "masks": {"email": "email"}},
        "case_matrix": [
            {"case": "case-1", "input": "raw text", "expect": {"name": "A"}},
        ],
        "epsilon": {"maturity": 0.1},
        "cited_lessons": [],
        "approved_by": "user:hieubt15",
        "approved_ts": "2026-07-14T10:00:00Z",
    }
    card.update(overrides)
    return card


# --- Tests Before (RED) ---------------------------------------------------

def test_append_recall_roundtrip_tier1(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    ids = []
    for i in range(3):
        result = _run(["append", "--type", "lesson", "--domain", "cv", "--surface", "extraction",
                        "--stack", "python", "--body", "lesson number %d" % i,
                        "--target", str(target)])
        assert result.returncode == 0, result.stderr
        ids.append(result.stdout.strip())
    assert len(set(ids)) == 3, "each append must mint a unique id"

    jsonl = target / "evals" / "_memory" / "lesson.jsonl"
    assert jsonl.is_file()

    recall = _run(["recall", "--filter", "domain=cv,surface=extraction", "--limit", "2",
                   "--type", "lesson", "--target", str(target)])
    assert recall.returncode == 0, recall.stderr
    lines = [l for l in recall.stdout.splitlines() if l.strip()]
    assert len(lines) == 2
    recs = [json.loads(l) for l in lines]
    required_fields = {"schema_version", "actor", "ts", "type", "id", "domain",
                        "surface", "stack", "card_hash", "body"}
    for r in recs:
        assert required_fields <= set(r)
        assert r["domain"] == "cv"
        assert r["surface"] == "extraction"
    assert {r["id"] for r in recs} <= set(ids)
    # the 2 NEWEST: the first appended id must be the one left out
    assert ids[0] not in {r["id"] for r in recs}


def test_standard_routes_tier2(tmp_path, monkeypatch):
    state_home = tmp_path / "state"
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state_home))

    result = _run(["append", "--type", "standard", "--domain", "harness", "--surface", "cli",
                   "--stack", "python", "--body", "always validate CLI args early"])
    assert result.returncode == 0, result.stderr

    jsonl = state_home / "eval-memory" / "standard.jsonl"
    assert jsonl.is_file()

    verify_home = _run(["verify-home"])
    assert verify_home.returncode == 0, verify_home.stderr
    assert str(state_home / "eval-memory") in verify_home.stdout


def test_body_masked_before_write(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    body = "contact an@example.com or 0912345678 for details"
    result = _run(["append", "--type", "lesson", "--domain", "d", "--surface", "s",
                   "--stack", "python", "--body", body, "--target", str(target)])
    assert result.returncode == 0, result.stderr

    raw = (target / "evals" / "_memory" / "lesson.jsonl").read_bytes()
    assert b"an@example.com" not in raw
    assert b"0912345678" not in raw
    assert b"*" in raw


def test_tolerant_read(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    good = _run(["append", "--type", "lesson", "--domain", "d", "--surface", "s",
                 "--stack", "python", "--body", "a valid lesson", "--target", str(target)])
    assert good.returncode == 0, good.stderr

    jsonl_path = target / "evals" / "_memory" / "lesson.jsonl"
    with open(jsonl_path, "a", encoding="utf-8") as fh:
        fh.write("not-json-garbage\n")

    recall = _run(["recall", "--filter", "domain=d", "--limit", "5", "--type", "lesson",
                   "--target", str(target)])
    assert recall.returncode == 0, recall.stderr
    lines = [l for l in recall.stdout.splitlines() if l.strip()]
    assert len(lines) == 1
    assert "skipped_corrupt: 1" in recall.stderr


def test_limit_required(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    result = _run(["recall", "--filter", "domain=d", "--type", "lesson", "--target", str(target)])
    assert result.returncode == 2


def test_append_only(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    first = _run(["append", "--type", "lesson", "--domain", "d", "--surface", "s",
                  "--stack", "python", "--body", "first", "--target", str(target)])
    assert first.returncode == 0, first.stderr
    jsonl = target / "evals" / "_memory" / "lesson.jsonl"
    prefix = jsonl.read_bytes()

    second = _run(["append", "--type", "lesson", "--domain", "d", "--surface", "s",
                   "--stack", "python", "--body", "second", "--target", str(target)])
    assert second.returncode == 0, second.stderr
    after = jsonl.read_bytes()
    assert after.startswith(prefix), "a 2nd append must never rewrite prior bytes"
    assert len(after) > len(prefix)


def test_self_target_fence(tmp_path):
    (tmp_path / "harness" / "plugins" / "hs" / "skills").mkdir(parents=True)
    result = _run(["append", "--type", "lesson", "--domain", "d", "--surface", "s",
                   "--stack", "python", "--body", "x", "--target", str(tmp_path)])
    assert result.returncode == 2
    assert not (tmp_path / "evals").exists()


def test_card_hash_auto(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    card_path = tmp_path / "card.json"
    card_path.write_text(json.dumps(_sample_card()), encoding="utf-8")

    write = subprocess.run(
        [sys.executable, str(_EVAL_CONFIG_SCRIPT), "write", "--target", str(target),
         "--card", str(card_path)],
        capture_output=True, text=True)
    assert write.returncode == 0, write.stderr

    hash_result = subprocess.run(
        [sys.executable, str(_EVAL_CONFIG_SCRIPT), "hash", "--target", str(target)],
        capture_output=True, text=True)
    assert hash_result.returncode == 0, hash_result.stderr
    expected = "sha256:" + hash_result.stdout.strip()

    append = _run(["append", "--type", "lesson", "--domain", "d", "--surface", "s",
                   "--stack", "python", "--body", "hash test", "--target", str(target),
                   "--auto-hash"])
    assert append.returncode == 0, append.stderr
    rid = append.stdout.strip()

    jsonl = target / "evals" / "_memory" / "lesson.jsonl"
    recs = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    rec = next(r for r in recs if r["id"] == rid)
    assert rec["card_hash"] == expected


def _spawn_bulk_worker(target_dir, count, tag):
    """Spawn a real OS process that calls eval_memory.cmd_append() `count`
    times in a loop -- this is what actually exercises the flock path under
    concurrent writers (two processes racing on the same fd, not just two
    in-process calls)."""
    code = textwrap.dedent("""
        import sys, argparse
        sys.path.insert(0, %(scripts_dir)r)
        import eval_memory
        for i in range(%(count)d):
            ns = argparse.Namespace(
                type="lesson", domain="d", surface="s", stack="python",
                body="%(tag)s-%%d" %% i, target=%(target)r,
                card_hash=None, auto_hash=False, tier=None)
            rc = eval_memory.cmd_append(ns)
            assert rc == 0
        """ % {
        "scripts_dir": str(_SCRIPTS_DIR),
        "count": count,
        "tag": tag,
        "target": str(target_dir),
    })
    return subprocess.Popen([sys.executable, "-c", code])


def test_concurrent_appends(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    p1 = _spawn_bulk_worker(target, 50, "a")
    p2 = _spawn_bulk_worker(target, 50, "b")
    rc1 = p1.wait(timeout=60)
    rc2 = p2.wait(timeout=60)
    assert rc1 == 0 and rc2 == 0

    jsonl = target / "evals" / "_memory" / "lesson.jsonl"
    lines = [l for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 100, "flock must serialize both writers -- no interleave, no loss"
    for l in lines:
        json.loads(l)  # every line must be independently parseable


def test_unicode_body(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    diacritics = "Bài học: chuẩn hoá số điện thoại VN — ưu tiên độ chính xác"
    result = _run(["append", "--type", "lesson", "--domain", "d", "--surface", "s",
                   "--stack", "python", "--body", diacritics, "--target", str(target)])
    assert result.returncode == 0, result.stderr

    jsonl = target / "evals" / "_memory" / "lesson.jsonl"
    text = jsonl.read_text(encoding="utf-8")
    assert diacritics in text, "ensure_ascii=False must keep diacritics literal"
    assert "\\u" not in text


def test_tier2_home_resolution_across_platforms(monkeypatch):
    """FORK B: proves the PATH-STRING branch selection for Linux/macOS/Windows
    via sys.platform + env monkeypatching. This does NOT exercise native
    locking (msvcrt is not importable on this Linux test runner) -- only that
    each OS branch resolves the documented base path."""
    em = _load()
    monkeypatch.delenv("HARNESS_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    # Linux, no XDG override -> default
    monkeypatch.setattr(em.sys, "platform", "linux")
    assert em._tier2_home() == Path("~/.local/share").expanduser() / "harness" / "eval-memory"

    # Linux, absolute XDG_DATA_HOME honored
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/xdg")
    assert em._tier2_home() == Path("/custom/xdg") / "harness" / "eval-memory"

    # Linux, RELATIVE XDG_DATA_HOME rejected -> falls back to default (the
    # guard from harness_paths.py:engine_home, pasted verbatim minus the
    # "absolutize" branch)
    monkeypatch.setenv("XDG_DATA_HOME", "relative/xdg")
    assert em._tier2_home() == Path("~/.local/share").expanduser() / "harness" / "eval-memory"
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    # Linux, "~/foo"-style XDG_DATA_HOME counts as absolute (expanduser BEFORE
    # isabs)
    monkeypatch.setenv("XDG_DATA_HOME", "~/xdg-home")
    assert em._tier2_home() == Path("~/xdg-home").expanduser() / "harness" / "eval-memory"
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    # macOS
    monkeypatch.setattr(em.sys, "platform", "darwin")
    assert em._tier2_home() == (
        Path("~/Library/Application Support").expanduser() / "harness" / "eval-memory")

    # Windows, LOCALAPPDATA set (forward-slash fake value -- this proves
    # branch + string composition only, not real Windows backslash parsing)
    monkeypatch.setattr(em.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "/fake/AppData/Local")
    assert em._tier2_home() == Path("/fake/AppData/Local") / "harness" / "eval-memory"

    # Windows, LOCALAPPDATA unset -> fallback ~/AppData/Local
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert em._tier2_home() == Path("~/AppData/Local").expanduser() / "harness" / "eval-memory"

    # HARNESS_STATE_DIR overrides every platform branch
    monkeypatch.setenv("HARNESS_STATE_DIR", "/tmp/harness-state-test")
    assert em._tier2_home() == Path("/tmp/harness-state-test") / "eval-memory"


def test_filter_none_sentinel_matches_null_card_hash():
    mod = _load()
    rec_null = {"card_hash": None, "domain": "d"}
    rec_hash = {"card_hash": "sha256:abc", "domain": "d"}
    assert mod._matches(rec_null, {"card_hash": "None"}) is True
    assert mod._matches(rec_null, {"card_hash": "null"}) is True
    assert mod._matches(rec_hash, {"card_hash": "None"}) is False
    assert mod._matches(rec_hash, {"card_hash": "sha256:abc"}) is True
