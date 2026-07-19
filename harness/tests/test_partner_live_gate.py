"""--live gate for the ccs partner lane's staged write path (phase 5).
`allow_live` (partner.yaml, env-restart bound) is the ONE real mechanical
gate: off refuses --live outright, even though --live is only an INTENT
marker the caller self-passes under a standing grant. Without --live the
write stays worktree-staged even when allow_live is on — proving the flag
is intent, not an auto-jump. CcsPrintTransport is faked via HARNESS_CCS_CMD
(never a real ccs call).
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


# --- allow_live:off refuses --live outright — the real gate ------------------
def test_live_refused_when_allow_live_off(repo, tmp_path, capsys):
    rc = pc.main(["task", "--live", "-p", "x", "--provider", "minimax",
                 "--config", _cfg_path(tmp_path, allow_live="off")])
    assert rc != 0
    assert "allow_live" in capsys.readouterr().err
    assert _git(repo, "status", "--porcelain") == ""


# --- allow_live:on + --live actually writes the LIVE tree --------------------
def test_live_writes_when_allow_live_on(repo, tmp_path):
    rc = pc.main(["task", "--live", "-p", "x", "--provider", "minimax",
                 "--config", _cfg_path(tmp_path, allow_live="on")])
    assert rc == 0
    assert (repo / "ccs_write_out.txt").exists()                # live tree DID change
    rec = _jobs()[-1]
    assert rec["provenance"]["mode"] == "live"
    assert rec["mode"] == "live"


# --- NO --live flag stays worktree-staged even with allow_live:on ------------
def test_no_live_flag_stays_worktree(repo, tmp_path):
    rc = pc.main(["task", "--write", "-p", "x", "--provider", "minimax",
                 "--config", _cfg_path(tmp_path, allow_live="on")])
    assert rc == 0
    assert not (repo / "ccs_write_out.txt").exists()             # live tree untouched
    assert _git(repo, "status", "--porcelain") == ""
    rec = _jobs()[-1]
    assert rec["provenance"]["mode"] == "write"
    assert rec["result"]["diff"].strip()                          # diff still returned


# --- advisory purpose + --live is refused, even with allow_live:on -----------
def test_advisory_purpose_live_refused(repo, tmp_path, capsys):
    rc = pc.main(["task", "--purpose", "critique", "--live", "-p", "x",
                 "--provider", "minimax",
                 "--config", _cfg_path(tmp_path, allow_live="on")])
    assert rc != 0
    assert "advisory" in capsys.readouterr().err.lower()
    assert _git(repo, "status", "--porcelain") == ""


# --- background + write is refused --------------------------------------------
def test_background_write_refused(repo, tmp_path, capsys):
    rc = pc.main(["task", "--background", "--write", "-p", "x",
                 "--provider", "minimax", "--config", _cfg_path(tmp_path)])
    assert rc != 0
    assert "background" in capsys.readouterr().err.lower()
