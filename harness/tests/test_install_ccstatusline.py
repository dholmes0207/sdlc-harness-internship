"""test_install_ccstatusline.py — opt-in ccstatusline onboarding.

When the operator opts in, the installer (1) wires a `statusLine` block into the
target's .claude/settings.json (the command auto-installs ccstatusline via npx) and
(2) copies a shipped default ccstatusline config into the user's config home. Both
writes are no-clobber: an existing statusLine or an existing config file is left
exactly as the user had it. Opt-out (the default) touches neither. The config home is
an explicit install() param so the home write is testable without touching ~/.config.
"""
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "harness" / "install"),
           str(_REPO_ROOT / "harness" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import install as installer  # noqa: E402
from conftest import _git  # noqa: E402

_ASSET = _REPO_ROOT / "harness" / "data" / "ccstatusline-default.json"


@pytest.fixture()
def target_repo(tmp_path):
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _settings(repo):
    return json.loads((repo / ".claude" / "settings.json").read_text())


# ---- the shipped default config asset ---------------------------------------

class TestDefaultAsset:
    def test_asset_exists_and_is_valid_v3_json(self):
        assert _ASSET.is_file(), "missing shipped ccstatusline default: %s" % _ASSET
        cfg = json.loads(_ASSET.read_text(encoding="utf-8"))
        assert cfg.get("version") == 3, "config must declare schema version 3"
        lines = cfg.get("lines")
        assert isinstance(lines, list) and lines, "lines must be a non-empty list"
        # WidgetItem[][]: each line is a list; each widget carries type + id
        for line in lines:
            assert isinstance(line, list)
            for w in line:
                assert isinstance(w.get("type"), str) and w["type"]
                assert isinstance(w.get("id"), str) and w["id"]


# ---- opt-in -----------------------------------------------------------------

class TestOptIn:
    def test_wires_statusline_block(self, target_repo, tmp_path):
        home = tmp_path / "cfg"
        res = installer.install(_REPO_ROOT, target_repo,
                                statusline=True, statusline_home=home)
        assert res["ok"]
        sl = _settings(target_repo).get("statusLine")
        assert sl and sl.get("type") == "command"
        assert "ccstatusline" in sl.get("command", "")

    def test_copies_default_config_into_home(self, target_repo, tmp_path):
        home = tmp_path / "cfg"
        installer.install(_REPO_ROOT, target_repo,
                          statusline=True, statusline_home=home)
        cfg = home / "settings.json"
        assert cfg.is_file(), "default ccstatusline config not copied"
        assert json.loads(cfg.read_text())["version"] == 3

    def test_preserves_hooks_when_wiring_statusline(self, target_repo, tmp_path):
        # statusLine wiring must not drop the hook wiring _wire_settings did
        installer.install(_REPO_ROOT, target_repo,
                          statusline=True, statusline_home=tmp_path / "cfg")
        assert _settings(target_repo).get("hooks"), "hooks lost after statusLine wire"


# ---- no-clobber -------------------------------------------------------------

class TestNoClobber:
    def test_existing_statusline_is_never_overwritten(self, target_repo, tmp_path):
        # first install wires the default
        installer.install(_REPO_ROOT, target_repo,
                          statusline=True, statusline_home=tmp_path / "a")
        # operator customizes it
        path = target_repo / ".claude" / "settings.json"
        s = json.loads(path.read_text())
        s["statusLine"] = {"type": "command", "command": "my-own-bar", "padding": 9}
        path.write_text(json.dumps(s, indent=2))
        # re-install must leave the custom statusLine intact
        installer.install(_REPO_ROOT, target_repo,
                          statusline=True, statusline_home=tmp_path / "b")
        assert _settings(target_repo)["statusLine"]["command"] == "my-own-bar"

    def test_existing_config_file_is_never_overwritten(self, target_repo, tmp_path):
        home = tmp_path / "cfg"
        home.mkdir()
        cfg = home / "settings.json"
        cfg.write_text('{"version": 3, "mine": true}', encoding="utf-8")
        installer.install(_REPO_ROOT, target_repo,
                          statusline=True, statusline_home=home)
        assert json.loads(cfg.read_text()).get("mine") is True


# ---- opt-out (default) ------------------------------------------------------

class TestOptOut:
    def test_default_install_wires_no_statusline(self, target_repo, tmp_path):
        installer.install(_REPO_ROOT, target_repo,
                          statusline_home=tmp_path / "cfg")
        assert "statusLine" not in _settings(target_repo)

    def test_default_install_writes_no_config(self, target_repo, tmp_path):
        home = tmp_path / "cfg"
        installer.install(_REPO_ROOT, target_repo, statusline_home=home)
        assert not (home / "settings.json").exists()
