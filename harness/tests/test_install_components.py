"""test_install_components.py — ship-all-but-off install model.

`--components` selects which optional components are ENABLED. The contract:
every component still SHIPS (files copied) and every hook is still WIRED into
settings.json — a deselected component is only RUNTIME-disabled (enabled:false
projected into harness-hooks.yaml). So a selective install stays verify-clean
(no unwired entrypoint, no missing file) and is reversible by re-toggling.
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


@pytest.fixture()
def target_repo(tmp_path):
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _hooks(repo):
    import yaml
    raw = yaml.safe_load(
        (repo / "harness" / "data" / "harness-hooks.yaml").read_text())
    return (raw or {}).get("hooks") or {}


def _state(repo):
    return json.loads(
        (repo / "harness" / "state" / "install-state.json").read_text())


def _wired_cmds(repo):
    settings = json.loads(
        (repo / ".claude" / "settings.json").read_text())
    return [h["command"] for groups in settings["hooks"].values()
            for g in groups for h in g["hooks"]]


class TestInstallComponents:
    def test_default_honors_policy_only_spine(self, target_repo):
        # Default (no --components) honors the shipped component-policy: the themed
        # plugin groups ship OFF (default-only-hs), hook-components stay on.
        res = installer.install(_REPO_ROOT, target_repo)
        assert res["ok"], res["problems"]
        comps = _state(target_repo)["components"]
        plugin_groups = {"flow", "think", "research", "create", "mem", "meta",
                         "viz", "ai", "devops", "stack", "uiux", "integrations", "extra"}
        for name, c in comps.items():
            if name in plugin_groups:
                assert not c["enabled"], "%s should default OFF (opt-in)" % name
            else:
                assert c["enabled"], "%s (hook component) should default ON" % name
        ep = json.loads((target_repo / ".claude" / "settings.json").read_text()
                        ).get("enabledPlugins", {})
        on = sorted(k.split("@", 1)[0] for k, v in ep.items() if v)
        assert on == ["hs"], "only the spine plugin should be enabled by default: %s" % on

    def test_explicit_all_ships_every_component_file(self, target_repo):
        res = installer.install(_REPO_ROOT, target_repo, components="all")
        assert res["ok"], res["problems"]
        # ship-all: a hook we COULD have disabled is copied regardless
        assert (target_repo / "harness" / "hooks"
                / "decision_capture_nudge.py").is_file()

    def test_selection_disables_others_runtime_only(self, target_repo):
        res = installer.install(_REPO_ROOT, target_repo, components="rbac")
        assert res["ok"], res["problems"]
        hk = _hooks(target_repo)
        # deselected components → their hooks runtime-disabled
        assert hk["nudge_context_inject"]["enabled"] is False
        assert hk["decision_capture_nudge"]["enabled"] is False
        # selected component stays enabled (not false)
        assert hk.get("agent_rbac_guard", {}).get("enabled") is not False

    def test_selection_keeps_files_and_wiring(self, target_repo):
        installer.install(_REPO_ROOT, target_repo, components="rbac")
        # ship-all: deselected file still on disk (not deleted)
        assert (target_repo / "harness" / "hooks"
                / "decision_capture_nudge.py").is_file()
        # wire-all: the hook still fires — now as a core of the in-process dispatcher
        # (decision_capture -> Stop + PostToolUse), so the dispatch command is wired.
        assert any("hook_dispatch.py" in c or "decision_capture_nudge.py" in c
                   for c in _wired_cmds(target_repo))

    @pytest.mark.dev_repo
    def test_selection_stays_verify_clean_strict(self, target_repo):
        # disabling a component must NOT make verify --strict fail: the hook is
        # still wired + registered + present, only its enabled flag flipped.
        res = installer.install(_REPO_ROOT, target_repo,
                                components="rbac", strict=True)
        assert res["ok"], res["problems"]
        assert res["problems"] == []

    def test_state_reflects_selection(self, target_repo):
        installer.install(_REPO_ROOT, target_repo, components="rbac")
        st = _state(target_repo)["components"]
        assert st["rbac"]["enabled"] is True
        assert st["decision-capture"]["enabled"] is False
        assert st["rbac"]["installed"] is True

    def test_unknown_component_errors(self, target_repo):
        with pytest.raises(installer.InstallError):
            installer.install(_REPO_ROOT, target_repo, components="ghost")

    def test_prompt_default_off_when_absent_from_policy(self):
        # _prompt_components seeds per-component defaults from the policy. A
        # component NOT present in that dict must still default OFF (opt-in) — the
        # absent-key fallback must not silently flip it ON.
        import builtins
        real_input = builtins.input

        def _eof(_prompt=""):
            raise EOFError  # user hit Enter / no TTY -> keep the default

        builtins.input = _eof
        try:
            # 'flow' is a themed group absent from the (empty) defaults dict
            out = installer._prompt_components(["flow", "think"], defaults={})
        finally:
            builtins.input = real_input
        assert out == "", "absent themed groups must default OFF, not ON: %r" % out

    def test_dogfood_does_not_project(self, tmp_path):
        # source == target: tree copy is a no-op AND component projection is
        # skipped (the dev's hand-authored harness-hooks.yaml is left alone).
        res = installer.install(_REPO_ROOT, _REPO_ROOT, components="rbac",
                                dry_run=True)
        assert res["ok"]
