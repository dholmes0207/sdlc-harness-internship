"""test_plugin_presence.py — a plugin declared in the marketplace must exist.

verify_install.plugin_presence_problems() NAMES each plugin that marketplace.json
declares but whose dir/plugin.json is absent (R8 install drift: a marketplace
that points at a missing plugin loads nothing, silently). [] when there is no
marketplace.json (not a plugin-aware tree).
"""
import json
import sys
from pathlib import Path

import pytest  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "harness" / "scripts"))

import verify_install as vi  # noqa: E402


def _mk(tmp_path, plugins, present):
    root = tmp_path / "r"
    mpdir = root / "harness" / "plugins" / ".claude-plugin"
    mpdir.mkdir(parents=True)
    (mpdir / "marketplace.json").write_text(json.dumps(
        {"name": "hs-local",
         "plugins": [{"name": n, "source": "./%s" % n} for n in plugins]}),
        encoding="utf-8")
    for n in present:
        pdir = root / "harness" / "plugins" / n / ".claude-plugin"
        pdir.mkdir(parents=True)
        (pdir / "plugin.json").write_text(json.dumps({"name": n}),
                                          encoding="utf-8")
        (root / "harness" / "plugins" / n / "skills").mkdir()
    return root


def test_all_present_clean(tmp_path):
    root = _mk(tmp_path, ["hs", "hs-viz"], present=["hs", "hs-viz"])
    assert vi.plugin_presence_problems(root) == []


def test_missing_plugin_dir_named(tmp_path):
    root = _mk(tmp_path, ["hs", "hs-viz"], present=["hs"])
    probs = vi.plugin_presence_problems(root)
    assert len(probs) == 1
    rel, prob = probs[0]
    assert "hs-viz" in rel and "hs-viz" in prob


def test_dir_without_plugin_json_flagged(tmp_path):
    root = _mk(tmp_path, ["hs"], present=["hs"])
    (root / "harness" / "plugins" / "hs" / ".claude-plugin" / "plugin.json").unlink()
    probs = vi.plugin_presence_problems(root)
    assert len(probs) == 1
    assert "plugin.json" in probs[0][1]


def test_dir_without_skills_or_agents_flagged(tmp_path):
    root = _mk(tmp_path, ["hs"], present=["hs"])
    (root / "harness" / "plugins" / "hs" / "skills").rmdir()
    probs = vi.plugin_presence_problems(root)
    assert len(probs) == 1
    assert "skills" in probs[0][1] or "agents" in probs[0][1]


def test_no_marketplace_returns_empty(tmp_path):
    root = tmp_path / "r"
    (root / "harness").mkdir(parents=True)
    assert vi.plugin_presence_problems(root) == []


def test_dot_prefixed_source_resolved_correctly(tmp_path):
    # source "./.hidden" must resolve to dir ".hidden", not "hidden"
    # (a char-set lstrip("./") would over-strip the leading dot).
    root = tmp_path / "r"
    mpdir = root / "harness" / "plugins" / ".claude-plugin"
    mpdir.mkdir(parents=True)
    (mpdir / "marketplace.json").write_text(json.dumps(
        {"name": "hs-local",
         "plugins": [{"name": "x", "source": "./.hidden"}]}), encoding="utf-8")
    pdir = root / "harness" / "plugins" / ".hidden" / ".claude-plugin"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text("{}", encoding="utf-8")
    (root / "harness" / "plugins" / ".hidden" / "skills").mkdir()
    assert vi.plugin_presence_problems(root) == []


def test_hook_only_plugin_not_flagged(tmp_path):
    # a plugin that ships only hooks/ (no skills/ or agents/) is loadable and
    # must NOT be flagged as "nothing to load".
    root = tmp_path / "r"
    mpdir = root / "harness" / "plugins" / ".claude-plugin"
    mpdir.mkdir(parents=True)
    (mpdir / "marketplace.json").write_text(json.dumps(
        {"name": "hs-local",
         "plugins": [{"name": "hookp", "source": "./hookp"}]}), encoding="utf-8")
    pdir = root / "harness" / "plugins" / "hookp" / ".claude-plugin"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text("{}", encoding="utf-8")
    (root / "harness" / "plugins" / "hookp" / "hooks").mkdir()
    assert vi.plugin_presence_problems(root) == []
