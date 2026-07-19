"""test_install_plugins.py — multi-plugin install wiring (ship-all-but-off).

install writes `extraKnownMarketplaces.hs-local` + `enabledPlugins` for EVERY
plugin declared in the local marketplace, into the SAME settings file the hooks
are wired into (one file, never split — both go through `_settings_path(target,
local)`). Ratified canonical (user, 2026-06-18): the default (no `--local`) is the
team-shared committed `settings.json`, so a fresh clone has plugins+gates on for
everyone; `--local` opts into the per-user gitignored `settings.local.json` (the
dogfood uses it because the harness repo gitignores all of `.claude/`).

Ship-all-but-off: every plugin defaults ENABLED; a component mapped to a plugin
(components.yaml `plugin:`) and deselected turns that plugin enabledPlugins:false
while the plugin DIR still ships. Core plugin `hs` is always enabled. Wiring is
idempotent and preserves user-authored enabledPlugins keys.
"""
import json
import sys
from pathlib import Path

import pytest  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "harness" / "install"),
           str(_REPO_ROOT / "harness" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import install as installer  # noqa: E402


def _mk_target(tmp_path, plugins, components=None):
    """Minimal target tree: a marketplace declaring `plugins` (list of names)
    plus an optional components.yaml mapping component -> plugin."""
    root = tmp_path / "target"
    (root / ".claude").mkdir(parents=True)
    mpdir = root / "harness" / "plugins" / ".claude-plugin"
    mpdir.mkdir(parents=True)
    mp = {"name": "hs-local",
          "plugins": [{"name": n, "source": "./%s" % n} for n in plugins]}
    (mpdir / "marketplace.json").write_text(json.dumps(mp), encoding="utf-8")
    for n in plugins:  # a real dir per plugin so "still ships" is observable
        pdir = root / "harness" / "plugins" / n / ".claude-plugin"
        pdir.mkdir(parents=True)
        (pdir / "plugin.json").write_text(json.dumps({"name": n}), encoding="utf-8")
        (root / "harness" / "plugins" / n / "skills").mkdir()
    if components is not None:
        import yaml
        ddir = root / "harness" / "data"
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / "components.yaml").write_text(
            yaml.safe_dump({"components": components}), encoding="utf-8")
    return root


def _settings(root, local=True):
    name = "settings.local.json" if local else "settings.json"
    p = root / ".claude" / name
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _wire(root, components_arg="all", local=True, dry_run=False):
    result = {"actions": [], "warnings": [], "problems": []}
    installer._wire_plugins(root, components_arg, local, result, dry_run)
    return result


def test_marketplace_plugins_lists_names(tmp_path):
    root = _mk_target(tmp_path, ["hs", "hs-extra"])
    assert set(installer._marketplace_plugins(root)) == {"hs", "hs-extra"}


def test_marketplace_plugins_empty_without_file(tmp_path):
    root = tmp_path / "bare"
    (root / "harness").mkdir(parents=True)
    assert installer._marketplace_plugins(root) == []


def test_wire_enables_every_plugin_by_default(tmp_path):
    root = _mk_target(tmp_path, ["hs", "hs-extra"])
    _wire(root)
    ep = _settings(root)["enabledPlugins"]
    assert ep == {"hs@hs-local": True, "hs-extra@hs-local": True}


def test_wire_writes_marketplace_directory_source(tmp_path):
    root = _mk_target(tmp_path, ["hs"])
    _wire(root)
    mk = _settings(root)["extraKnownMarketplaces"]["hs-local"]
    assert mk == {"source": {"source": "directory", "path": "./harness/plugins"}}


def test_component_off_disables_its_plugin_dir_still_ships(tmp_path):
    comps = {"viz": {"plugin": "hs-extra", "skills": ["x"]},
             "other": {"hooks": []}}
    root = _mk_target(tmp_path, ["hs", "hs-extra"], components=comps)
    _wire(root, components_arg="other")          # viz deselected
    ep = _settings(root)["enabledPlugins"]
    assert ep["hs-extra@hs-local"] is False
    assert ep["hs@hs-local"] is True             # core always on
    assert (root / "harness" / "plugins" / "hs-extra").is_dir()  # still ships


def test_wire_is_idempotent(tmp_path):
    root = _mk_target(tmp_path, ["hs", "hs-extra"])
    _wire(root)
    _wire(root)
    ep = _settings(root)["enabledPlugins"]
    assert ep == {"hs@hs-local": True, "hs-extra@hs-local": True}


def test_wire_preserves_user_enabled_plugins_key(tmp_path):
    root = _mk_target(tmp_path, ["hs"])
    p = root / ".claude" / "settings.local.json"
    p.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}),
                 encoding="utf-8")
    _wire(root)
    ep = _settings(root)["enabledPlugins"]
    assert ep["foo@bar"] is True
    assert ep["hs@hs-local"] is True


def test_core_plugin_forced_on_even_if_a_component_maps_it(tmp_path):
    comps = {"corecomp": {"plugin": "hs"}, "other": {"hooks": []}}
    root = _mk_target(tmp_path, ["hs"], components=comps)
    _wire(root, components_arg="other")          # corecomp deselected
    assert _settings(root)["enabledPlugins"]["hs@hs-local"] is True


def test_wire_dry_run_writes_nothing(tmp_path):
    root = _mk_target(tmp_path, ["hs", "hs-extra"])
    _wire(root, dry_run=True)
    assert _settings(root) == {}                 # no settings file written


def test_chosen_components_empty_csv_selects_none():
    comps = {"a": {"hooks": []}, "b": {"hooks": []}}
    assert installer._chosen_components(comps, "") == set()      # not "all"
    assert installer._chosen_components(comps, None) == set(comps)
    assert installer._chosen_components(comps, "all") == set(comps)


def test_core_not_injected_when_absent_from_marketplace(tmp_path):
    root = _mk_target(tmp_path, ["hs-extra"])     # marketplace has NO core "hs"
    _wire(root)
    ep = _settings(root)["enabledPlugins"]
    assert "hs@hs-local" not in ep                # no phantom enable key
    assert ep["hs-extra@hs-local"] is True


def test_wire_default_lands_in_committed_settings_json(tmp_path):
    # Ratified canonical (user, 2026-06-18): without --local, plugin enable goes to
    # the team-shared committed settings.json (same file as hooks), NOT scattered into
    # the per-user settings.local.json. Locks COOK-VERIFY #2 against future drift.
    root = _mk_target(tmp_path, ["hs", "hs-extra"])
    _wire(root, local=False)
    assert _settings(root, local=False)["enabledPlugins"] == {
        "hs@hs-local": True, "hs-extra@hs-local": True}
    assert not (root / ".claude" / "settings.local.json").is_file()
