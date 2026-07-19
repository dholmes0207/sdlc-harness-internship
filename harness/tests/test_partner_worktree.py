"""Worktree-staged write path (phase 5) for the ccs partner lane —
`task --write` stages ccs's delegated edits in a throwaway git worktree,
returns the diff, and leaves the LIVE tree untouched by default. Mirrors
test_gemini_worktree_jail.py's temp-repo pattern; CcsPrintTransport is
faked via HARNESS_CCS_CMD -> fixtures/fake_ccs.py (never a real ccs call).
"""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import partner_companion as pc  # noqa: E402
import partner_preflight as pf  # noqa: E402

_FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_ccs.py"

_BASE_CFG = {
    "master": "on", "write": "worktree_staged", "allow_live": "off",
    "secret_scrub": "warn",
    "purposes": {"review": "review", "adversarial-review": "redteam",
                 "research": "research", "critique": "critique"},
    "timeouts": {"default": 5}, "retry": {"max_attempts": 1, "on_markers": []},
    "cost_warn_usd": 0.50,
}


def _cfg_path(tmp_path, **over):
    p = tmp_path / "partner.yaml"
    p.write_text(yaml.safe_dump({**_BASE_CFG, **over}, sort_keys=False), encoding="utf-8")
    return str(p)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("HARNESS_CCS_CMD", "%s %s" % (sys.executable, _FAKE))
    monkeypatch.delenv("FAKE_CCS_MODE", raising=False)
    monkeypatch.setattr(pf, "discover_providers", lambda: ["minimax"])


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


def _jobs():
    return pc.JobRegistry(subdir="partner").read_all()


# --- staged write returns a non-empty diff; the LIVE tree stays untouched ----
def test_staged_write_returns_diff_live_untouched(repo, tmp_path):
    rc = pc.main(["task", "--write", "-p", "add a file", "--provider", "minimax",
                 "--config", _cfg_path(tmp_path)])
    assert rc == 0
    rec = _jobs()[-1]
    assert rec["status"] == "done"
    assert rec["result"]["diff"].strip()
    assert "ccs_write_out.txt" in rec["result"]["diff"]
    assert rec["provenance"]["mode"] == "write"
    assert _git(repo, "status", "--porcelain") == ""            # live tree untouched proof
    assert "worktrees" not in _git(repo, "worktree", "list")    # cleaned up


# --- an empty diff RAISES — never a silent "done" -----------------------------
def test_empty_diff_raises(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CCS_MODE", "no_write")
    rc = pc.main(["task", "--write", "-p", "x", "--provider", "minimax",
                 "--config", _cfg_path(tmp_path)])
    assert rc != 0
    rec = _jobs()[-1]
    assert rec["status"] == "failed"
    assert "empty" in rec["reason"].lower()
    assert _git(repo, "status", "--porcelain") == ""


# --- advisory purpose + write is refused --------------------------------------
def test_advisory_purpose_write_refused(repo, tmp_path, capsys):
    rc = pc.main(["task", "--purpose", "review", "--write", "-p", "x",
                 "--provider", "minimax", "--config", _cfg_path(tmp_path)])
    assert rc != 0
    assert "advisory" in capsys.readouterr().err.lower()


# --- a failed retry attempt's partial write must not leak into the diff ------
def test_retry_does_not_fold_partial_write_into_diff(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CCS_MODE", "transient_once")
    monkeypatch.setenv("FAKE_CCS_MARKER", str(tmp_path / "marker"))
    cfg_path = _cfg_path(tmp_path, retry={"max_attempts": 2,
                                          "on_markers": ["rate_limit"]})
    rc = pc.main(["task", "--write", "-p", "x", "--provider", "minimax",
                 "--config", cfg_path])
    assert rc == 0
    rec = _jobs()[-1]
    assert rec["status"] == "done"
    diff = rec["result"]["diff"]
    assert "ccs_write_out.txt" in diff
    assert "stray.txt" not in diff


# --- an escaped staged write must NOT apply to the live tree -----------------
def test_escaped_write_refuses_apply_live(repo, tmp_path, monkeypatch):
    # fake_ccs writes the normal in-worktree file AND (via FAKE_CCS_ESCAPE_FILE)
    # an absolute-path write straight into repo_root — simulating ccs's own
    # tools reaching outside the worktree jail during a --live turn.
    escape_target = repo / "escaped.txt"
    monkeypatch.setenv("FAKE_CCS_ESCAPE_FILE", str(escape_target))
    rc = pc.main(["task", "--write", "--live", "-p", "x", "--provider", "minimax",
                 "--config", _cfg_path(tmp_path, allow_live="on")])
    assert rc != 0
    rec = _jobs()[-1]
    assert rec["status"] == "failed"
    assert "escap" in rec["reason"].lower()
    # The diff's own intended file must NOT have been applied to the live
    # tree — an escaped diff is untrustworthy/incomplete, never applied.
    assert not (repo / "ccs_write_out.txt").exists()
