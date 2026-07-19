"""test_auto_decision_log.py — advisory sink for AI-autonomous decisions.

An auto-mode (default cook, hs:fix, hs:code-review …) makes decisions with no human in
the loop. Nothing records them for a next-morning skim. This sink appends ONE JSONL line
per decision — closed-vocab label + forced evidence — mirroring emit_observation's
fail-loud/fail-open contract. The label vocab is CLOSED (a hand-edited YAML): an
out-of-vocab label is a typo, so it fails LOUD (exit 2, no write); a store I/O failure is
telemetry-class, so it fails OPEN (exit 0). The store lives WITH the plan, and the path
resolves to the MAIN worktree via git-common-dir so a line emitted from a linked worktree
(which git-removes after the run) is never lost.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import auto_decision_log as adl  # noqa: E402


# --------------------------------------------------------------------------- helpers
def _labels_file(tmp_path):
    """Ship the closed-vocab labels file the loader reads."""
    return Path(_SCRIPTS).parent / "data" / "auto-decision-labels.yaml"


def _valid_args(store, labels, **over):
    base = dict(skill="hs:cook", mode="auto", label="ARCH",
                what="split module boundary", why="cohesion",
                evidence="foo.py:12")
    base.update(over)
    argv = ["--store", str(store), "--labels", str(labels)]
    for k in ("skill", "mode", "label", "what", "why", "evidence"):
        argv += ["--%s" % k.replace("_", "-"), str(base[k])]
    if base.get("in_plan"):
        argv.append("--in-plan")
    if base.get("session"):
        argv += ["--session", str(base["session"])]
    return argv


# --------------------------------------------------------------------------- vocab
def test_labels_loader_returns_basket_map():
    m = adl.load_labels()
    assert isinstance(m, dict)
    assert len(m) == 8
    assert m["ARCH"] == "must_review"
    assert m["TRIVIAL"] == "trace_only"
    assert set(m.values()) == {"must_review", "trace_only"}


# --------------------------------------------------------------------------- fail-loud
def test_out_of_vocab_label_exit2_no_write(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    rc = adl.main(_valid_args(store, adl._DEFAULT_LABELS, label="NOPE"))
    assert rc == 2
    assert not store.exists() or store.read_text() == ""


def test_missing_evidence_exit2_no_write(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    rc = adl.main(_valid_args(store, adl._DEFAULT_LABELS, evidence=""))
    assert rc == 2
    assert not store.exists() or store.read_text() == ""


def test_over_cap_exit2(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    rc = adl.main(_valid_args(store, adl._DEFAULT_LABELS, why="x" * 3000))
    assert rc == 2
    assert not store.exists() or store.read_text() == ""


# --------------------------------------------------------------------------- happy path
def test_valid_append_shape(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    rc = adl.main(_valid_args(store, adl._DEFAULT_LABELS))
    assert rc == 0
    lines = store.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    required = {"type", "id", "ts", "actor", "skill", "mode",
                "label", "in_plan", "reviewed", "what", "why", "evidence"}
    assert required <= set(rec)
    assert rec["type"] == "decision"
    assert rec["reviewed"] is False
    assert rec["in_plan"] is False
    assert rec["id"]
    assert rec["label"] == "ARCH"


def test_actor_ts_present(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    adl.main(_valid_args(store, adl._DEFAULT_LABELS))
    rec = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    assert rec["actor"].startswith("user:") or rec["actor"] == "ci"
    # ts is an ISO-8601 stamp
    from datetime import datetime
    datetime.fromisoformat(rec["ts"])


def test_in_plan_flag(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    adl.main(_valid_args(store, adl._DEFAULT_LABELS, in_plan=True))
    rec = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    assert rec["in_plan"] is True


# --------------------------------------------------------------------------- fold
def test_fold_state_overlays_review():
    events = [
        {"type": "decision", "id": "abc123", "reviewed": False, "label": "ARCH"},
        {"type": "review", "target": "abc123", "reviewed": True},
    ]
    folded = {d["id"]: d for d in adl.fold_state(events)}
    assert folded["abc123"]["reviewed"] is True


def test_fold_state_star_reviews_all():
    events = [
        {"type": "decision", "id": "a1", "reviewed": False},
        {"type": "decision", "id": "a2", "reviewed": False},
        {"type": "review", "target": "*", "reviewed": True},
    ]
    folded = {d["id"]: d for d in adl.fold_state(events)}
    assert folded["a1"]["reviewed"] is True
    assert folded["a2"]["reviewed"] is True


def test_load_events_roundtrip(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    adl.main(_valid_args(store, adl._DEFAULT_LABELS))
    events = adl.load_events(store)
    assert len(events) == 1
    assert events[0]["type"] == "decision"
    # missing file → empty, never raises
    assert adl.load_events(tmp_path / "nope.jsonl") == []


# --------------------------------------------------------------------------- IO fail-open
def test_io_failure_exit0(tmp_path):
    # a store path whose parent cannot be created → OSError → fail-open exit 0
    rc = adl.main(_valid_args("/proc/nonexistent-adl/x.jsonl", adl._DEFAULT_LABELS))
    assert rc == 0


# --------------------------------------------------------------------------- toggle
def test_disabled_toggle_noop_exit0(tmp_path):
    cfg = tmp_path / "auto-decision.yaml"
    cfg.write_text("enabled: false\n", encoding="utf-8")
    store = tmp_path / "auto-decisions.jsonl"
    rc = adl.main(_valid_args(store, adl._DEFAULT_LABELS) + ["--config", str(cfg)])
    assert rc == 0
    assert not store.exists() or store.read_text() == ""


def test_enabled_toggle_writes(tmp_path):
    cfg = tmp_path / "auto-decision.yaml"
    cfg.write_text("enabled: true\n", encoding="utf-8")
    store = tmp_path / "auto-decisions.jsonl"
    rc = adl.main(_valid_args(store, adl._DEFAULT_LABELS) + ["--config", str(cfg)])
    assert rc == 0
    assert len(store.read_text(encoding="utf-8").splitlines()) == 1


# --------------------------------------------------------------------------- touched-flag
def test_touched_flag_set_on_append(tmp_path, monkeypatch):
    # the touched-flag is keyed on the STORE PATH (not the session): the sink runs in the
    # Bash-tool env with no session id, so a session-keyed flag would never match the hook's.
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    store = tmp_path / "auto-decisions.jsonl"
    assert adl.touched_flag_set(store) is False
    adl.main(_valid_args(store, adl._DEFAULT_LABELS, session="sess-XYZ"))
    assert adl.touched_flag_set(store) is True


def test_reports_store_has_no_session_in_filename(tmp_path):
    # a per-session reports filename would desync the sessionless Bash-tool writer from the
    # hook-env reader; the reports ledger is a single shared append-only file (each record
    # still carries its own session field).
    store = adl._reports_store(tmp_path)
    assert store == tmp_path / "plans" / "reports" / "auto-decisions.jsonl"
    assert "unknown" not in store.name  # no _safe_session leaked into the filename


# --------------------------------------------------------------------------- worktree resolve
def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _repo_with_worktree(tmp_path, status="in_progress"):
    """A real main repo with an in_progress plan + a linked worktree branched off HEAD."""
    main = tmp_path / "main"
    main.mkdir()
    _git(["init", "-q"], main)
    _git(["config", "user.email", "t@t.t"], main)
    _git(["config", "user.name", "t"], main)
    slug = "260101-0000-probe-plan"
    plan_dir = main / "plans" / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text(
        "---\nid: %s\nstatus: %s\n---\n# p\n" % (slug, status), encoding="utf-8")
    _git(["add", "-A"], main)
    _git(["commit", "-qm", "init"], main)
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-q", str(wt), "HEAD"], main)
    return main, wt, slug


def test_resolve_uses_git_common_dir_from_worktree(tmp_path, monkeypatch):
    main, wt, slug = _repo_with_worktree(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(wt)
    store = adl.resolve_store()
    store_r = str(Path(store).resolve())
    assert store_r.startswith(str(main.resolve()))
    assert not store_r.startswith(str(wt.resolve()))
    assert slug in store_r


def test_worktree_write_rejected_or_routed(tmp_path, monkeypatch):
    main, wt, slug = _repo_with_worktree(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(main)
    # a caller hands a plan-dir that points INTO the linked worktree → must NOT write there
    store = adl.resolve_store(plan_dir=str(wt / "plans" / slug))
    store_r = str(Path(store).resolve())
    assert not store_r.startswith(str(wt.resolve()))
    assert store_r.startswith(str(main.resolve()))


def test_nested_worktree_write_refused(tmp_path, monkeypatch):
    # a plan-dir inside a worktree NESTED under main (main/.claude/worktrees/*) must be refused
    # too — it is under the main tree, so the outside-main check alone would let it through; the
    # linked-worktree detector is what catches it. Would write into the removable tree without it.
    main = tmp_path / "main"
    main.mkdir()
    _git(["init", "-q"], main)
    _git(["config", "user.email", "t@t.t"], main)
    _git(["config", "user.name", "t"], main)
    slug = "260101-0000-nested"
    (main / "plans" / slug).mkdir(parents=True)
    (main / "plans" / slug / "plan.md").write_text(
        "---\nid: %s\nstatus: in_progress\n---\n# p\n" % slug, encoding="utf-8")
    _git(["add", "-A"], main)
    _git(["commit", "-qm", "init"], main)
    nested = main / ".claude" / "worktrees" / "cook-x"
    nested.parent.mkdir(parents=True)
    _git(["worktree", "add", "-q", str(nested), "HEAD"], main)

    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(main)  # so _main_tree_root() resolves to THIS main
    store = adl.resolve_store(plan_dir=str(nested / "plans" / slug))
    store_r = str(Path(store).resolve())
    assert not store_r.startswith(str(nested.resolve()))  # never inside the removable worktree
    assert store_r.startswith(str(main.resolve()))
    assert "reports" in store_r
