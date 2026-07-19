"""Worktree-staging write path (phase 5) — the RT-01 mitigation.

sandbox_write stages gemini's edits in a throwaway git worktree, returns the
diff, and leaves the live tree UNTOUCHED (T2 is the RT-01 proof). Honest scope:
this blocks accidents + gates via diff; it is NOT an OS sandbox (a yolo gemini
could still escape cwd — escape-scan warns, real adversarial proof is live-only).
Guards: advisory+write refused (S5); a write-transport failure RAISES, never a silent
"done" (RT-U1); read_only writes nothing (D1). GeminiPrintTransport is faked.
"""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import gemini_companion as gc  # noqa: E402
import gemini_transport as gt  # noqa: E402

_BASE = {
    "master": "on", "mode": "partner", "write": "read_only", "stop_review_gate": "off",
    "purposes": {"research": "flash", "scout": "flash", "review": "pro",
                 "critique": "pro", "redteam": "pro", "delegate": "pro", "fix": "pro"},
    "route_all_surface": ["research", "scout"], "overrides": {},
    "timeouts": {"default": 5}, "retry": {"max_attempts": 1, "on_markers": []},
    "secret_scrub": "warn",
}


def _cfg(tmp_path, **over):
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(yaml.safe_dump({**_BASE, **over}, sort_keys=False), encoding="utf-8")
    return str(p)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(root)
    return root


def _writing_fake(filename="gemini_out.txt", content="hello from gemini\n"):
    """A fake gemini print transport that writes a file into its cwd — but ONLY in a
    write mode (yolo/write), exactly like a real `gemini -p --approval-mode yolo`: the
    read-only 'plan' surface writes nothing."""
    class _T:
        def __init__(self):
            pass

        def run(self, *, composed, mode, session, cwd, timeout, model, engine_cfg):
            if mode in ("yolo", "write") and cwd:
                (Path(cwd) / filename).write_text(content, encoding="utf-8")
                return gt.RunResult(content={"text": "wrote %s" % filename},
                                    session="sess-1")
            return gt.RunResult(content={"text": "proposal only (read-only)"},
                                session="sess-1")
    return _T


# --- T1: sandbox write returns the diff of gemini's edit ---------------------
def test_t1_sandbox_write_returns_diff(repo, monkeypatch, capsys):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _writing_fake())
    rc = gc.main(["task", "--write", "-p", "add a file", "--config",
                  _cfg(repo.parent, write="sandbox_write")])
    assert rc == 0
    rec = gc.JobRegistry().read_all()[-1]
    assert rec["status"] == "done"
    assert "gemini_out.txt" in rec["result"]["diff"]


# --- T2: the live tree is untouched; the worktree is cleaned up (RT-01) ------
def test_t2_live_tree_unchanged_worktree_gone(repo, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _writing_fake())
    gc.main(["task", "--write", "-p", "x", "--config",
             _cfg(repo.parent, write="sandbox_write")])
    assert _git(repo, "status", "--porcelain") == ""     # live tree clean
    wt = Path(gc.JobRegistry()._dir) / "worktrees"
    assert not any(wt.glob("*")) if wt.exists() else True  # worktree removed
    # git no longer tracks a stale linked worktree
    assert "worktrees" not in _git(repo, "worktree", "list")


# --- T3: advisory purpose + write is refused (S5) ---------------------------
def test_t3_advisory_write_refused(repo, monkeypatch, capsys):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _writing_fake())
    rc = gc.main(["task", "--purpose", "review", "--write", "-p", "x", "--config",
                  _cfg(repo.parent, write="sandbox_write")])
    assert rc != 0
    assert "S5" in capsys.readouterr().err


# --- T4: background + write is refused (RT-09) ------------------------------
def test_t4_background_write_refused(repo, monkeypatch, capsys):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _writing_fake())
    rc = gc.main(["task", "--background", "--write", "-p", "x", "--config",
                  _cfg(repo.parent, write="sandbox_write")])
    assert rc != 0
    assert "background" in capsys.readouterr().err.lower()


# --- T5: a write-transport failure RAISES and still cleans the worktree ------
def test_t5_write_failure_raises_and_cleans(repo, monkeypatch):
    class _BadWrite:
        def __init__(self):
            pass

        def run(self, **kw):
            raise gc.AcpError("write rejected")
    monkeypatch.setattr(gc, "GeminiPrintTransport", _BadWrite)
    reg = gc.JobRegistry()
    with pytest.raises(gc.AcpError):
        gc._run_sandbox_write(reg, "jobX", "delegate", "x",
                              gc._cfgmod.effective(gc._cfgmod.resolve(
                                  _cfg(repo.parent, write="sandbox_write"))),
                              str(repo))
    wt = Path(reg._dir) / "worktrees"
    assert not any(wt.glob("*")) if wt.exists() else True
    assert "worktrees" not in _git(repo, "worktree", "list")


# --- T6: read_only default writes nothing (D1) ------------------------------
def test_t6_read_only_writes_nothing(repo, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _writing_fake())
    rc = gc.main(["task", "-p", "x", "--config", _cfg(repo.parent)])  # default read_only
    assert rc == 0
    assert _git(repo, "status", "--porcelain") == ""
    wt = Path(gc.JobRegistry()._dir) / "worktrees"
    assert not wt.exists() or not any(wt.glob("*"))  # no worktree created at all


# --- Phase 5: agy (agy-print) IS a write citizen now (abspath + strip SSH_*) ---
# A live re-probe (memory agy-print-cannot-write-confirmed, flipped) showed agy DOES
# land a file when the write-target is an ABSOLUTE path in the prompt (agy ignores
# cwd) AND SSH_* is stripped from its env (SSH_* → file-token-storage → auth dies).
# The worktree jail + escape-scan hold; a NEW empty-diff guard (F4) catches the case
# where agy wrote to scratch OUTSIDE the repo (escape-scan is blind there).
_FAKE_AGY = Path(__file__).resolve().parent / "fixtures" / "fake_agy.py"


def _agy_cmd(*extra):
    return "python3 %s %s" % (_FAKE_AGY, " ".join(str(x) for x in extra))


# --- P5-T1: agy write lands a file in the worktree (abspath contract) --------
def test_p5t1_agy_write_lands_file_in_worktree(repo, monkeypatch):
    monkeypatch.setenv("HARNESS_AGY_CMD", _agy_cmd())
    rc = gc.main(["task", "--write", "--engine", "agy-print", "-p", "add a file",
                  "--config", _cfg(repo.parent, write="sandbox_write")])
    assert rc == 0
    rec = gc.JobRegistry().read_all()[-1]
    assert rec["status"] == "done"
    assert "agy_out.txt" in rec["result"]["diff"]     # agy's file staged in the worktree
    assert rec["result"]["escaped"] is False
    assert rec["provenance"]["engine"] == "agy"
    assert _git(repo, "status", "--porcelain") == ""  # live tree untouched


# --- P5-T2: agy + write is no longer refused at the chokepoint ---------------
def test_p5t2_agy_write_no_longer_refused(repo, monkeypatch):
    monkeypatch.setenv("HARNESS_AGY_CMD", _agy_cmd())
    cfg = gc._cfgmod.effective(gc._cfgmod.resolve(
        _cfg(repo.parent, write="sandbox_write", engine="agy-print")))
    out = gc.partner_call("delegate", "x", mode="yolo", cwd=str(repo), cfg=cfg)
    assert out.status == "ok"                    # ran, no S5-style advisory-only refusal
    assert out.provenance["engine"] == "agy"


# --- P5-T3: the write prompt carries the worktree ABSPATH (agy ignores cwd) --
def test_p5t3_write_prompt_carries_worktree_abspath(repo, monkeypatch):
    captured = {}
    real = gc.partner_call

    def spy(purpose, prompt, **kw):
        captured["prompt"] = prompt
        captured["cwd"] = kw.get("cwd")
        return real(purpose, prompt, **kw)
    monkeypatch.setattr(gc, "partner_call", spy)
    monkeypatch.setenv("HARNESS_AGY_CMD", _agy_cmd())
    gc.main(["task", "--write", "--engine", "agy-print", "-p", "x",
             "--config", _cfg(repo.parent, write="sandbox_write")])
    assert "[sandbox-write-dir]" in captured["prompt"]        # the marker is injected
    assert captured["cwd"] and captured["cwd"] in captured["prompt"]  # abspath carried


# --- P5-T4: an in-repo escape is still caught (jail holds) -------------------
def test_p5t4_agy_write_escape_detected(repo, monkeypatch, capsys):
    # agy writes to the worktree AND escapes to the repo root; escape-scan flags it.
    monkeypatch.setenv("HARNESS_AGY_CMD", _agy_cmd("--escape-to", str(repo)))
    rc = gc.main(["task", "--write", "--engine", "agy-print", "-p", "x",
                  "--config", _cfg(repo.parent, write="sandbox_write")])
    assert rc == 0
    rec = gc.JobRegistry().read_all()[-1]
    assert rec["result"]["escaped"] is True
    assert "ESCAPE" in capsys.readouterr().err
    # the escape is cleaned from the live tree by the fixture's git baseline check
    _git(repo, "checkout", "--", ".")
    _git(repo, "clean", "-fdq")


# --- P5-T5 (F4, ship-blocker): an empty worktree diff RAISES, never silent-ok -
def test_p5t5_write_empty_diff_raises(repo, monkeypatch):
    # agy writes to scratch OUTSIDE the repo → the worktree diff is empty and the
    # escape-scan is blind (repo_root unchanged). Must RAISE, never report "done".
    scratch = repo.parent / "outside-scratch"
    monkeypatch.setenv("HARNESS_AGY_CMD", _agy_cmd("--write-elsewhere", str(scratch)))
    rc = gc.main(["task", "--write", "--engine", "agy-print", "-p", "x",
                  "--config", _cfg(repo.parent, write="sandbox_write")])
    assert rc != 0
    rec = gc.JobRegistry().read_all()[-1]
    assert rec["status"] == "failed"
    assert "empty" in rec["reason"].lower()


# --- P5-T6: SSH_* is stripped from agy's env (else file-token auth dies) ------
def test_p5t6_agy_env_strips_ssh(repo, monkeypatch):
    monkeypatch.setenv("SSH_CLIENT", "10.0.0.1 22 22")  # a live SSH session's leak
    monkeypatch.setenv("HARNESS_AGY_CMD", _agy_cmd("--fail-on-ssh"))
    rc = gc.main(["task", "--write", "--engine", "agy-print", "-p", "add a file",
                  "--config", _cfg(repo.parent, write="sandbox_write")])
    # if the transport did NOT strip SSH_*, the fake exits 1 (auth fail) → job failed
    assert rc == 0
    assert gc.JobRegistry().read_all()[-1]["status"] == "done"


# --- P5-T7: gemini write still works when pinned (regression) ----------------
def test_p5t7_write_uses_gemini_when_pinned(repo, monkeypatch):
    monkeypatch.setattr(gc, "GeminiPrintTransport", _writing_fake())
    rc = gc.main(["task", "--write", "--engine", "gemini-print", "-p", "add a file",
                  "--config", _cfg(repo.parent, write="sandbox_write")])
    assert rc == 0
    rec = gc.JobRegistry().read_all()[-1]
    assert rec["status"] == "done"
    assert "gemini_out.txt" in rec["result"]["diff"]
    assert rec["provenance"]["engine"] == "gemini"
