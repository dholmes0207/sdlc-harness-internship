"""Rename guard: hs:ui-ux-pro-max -> hs:ui-ux across the live hs wiring.

A mechanical rename touches ~28 files; the runtime `scripts/search.py` path is
the load-bearing one — a missed path segment silently breaks UI-search at run
time. Per the two-tier gating this asserts ONLY on hs-namespaced identity
(skill key, deps, `hs:ui-ux-pro-max` refs) and runtime paths
(`skills/ui-ux-pro-max/`). Historical prose in decisions.* and external huashu
refs are advisory and out of scope here.
"""
import sys
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "harness" / "scripts"))

import skill_deps  # noqa: E402
import component_config as cc  # noqa: E402

_HS = REPO_ROOT / "harness" / "plugins" / "hs"
_DEPS = REPO_ROOT / "harness" / "data" / "skill-deps.yaml"
_SCAN_SUFFIXES = {".md", ".ts", ".yaml"}


def _active_tree_files():
    for p in _HS.rglob("*"):
        if p.suffix in _SCAN_SUFFIXES and p.name != "manifest.json" and p.is_file():
            yield p


@pytest.mark.dev_repo
def test_ui_ux_dir_renamed():
    assert (_HS / "skills" / "ui-ux" / "SKILL.md").is_file(), \
        "renamed skill must live at hs/skills/ui-ux/SKILL.md"
    assert not (_HS / "skills" / "ui-ux-pro-max").exists(), \
        "old ui-ux-pro-max/ directory must be gone (git mv, no copy left behind)"


@pytest.mark.dev_repo
def test_ui_ux_identity_frontmatter():
    text = (_HS / "skills" / "ui-ux" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: hs:ui-ux\n" in text, "SKILL.md frontmatter must declare name: hs:ui-ux"
    assert "name: hs:ui-ux-pro-max" not in text, "stale name: hs:ui-ux-pro-max must be gone"
    assert "name: ui-ux-pro-max\n" not in text, "stale bare name: ui-ux-pro-max must be gone"


def test_skill_deps_key_renamed():
    skills = skill_deps.load_deps(_DEPS)["skills"]
    assert "ui-ux" in skills, "skill-deps.yaml must carry the ui-ux key"
    assert "ui-ux-pro-max" not in skills, "old ui-ux-pro-max key must be gone from skill-deps.yaml"
    for src in ("design", "frontend-design", "stitch"):
        deps = set(skills[src].get("deps", []))
        assert "ui-ux" in deps, f"{src} deps must point at ui-ux"
        assert "ui-ux-pro-max" not in deps, f"{src} deps must not point at ui-ux-pro-max"


def test_components_uiux_group_renamed():
    uiux = cc.load_components().get("uiux", {}).get("skills", [])
    assert "ui-ux" in uiux, "uiux group in components.yaml must list ui-ux"
    assert "ui-ux-pro-max" not in uiux, "uiux group must not still list ui-ux-pro-max"


def test_no_stale_namespaced_ref_or_runtime_path():
    """No `hs:ui-ux-pro-max` reference and no `skills/ui-ux-pro-max/` runtime
    path survives anywhere in the active hs tree (manifest.json excluded — it
    is machine-rebuilt in Phase 3)."""
    ns_offenders, path_offenders = [], []
    for p in _active_tree_files():
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "hs:ui-ux-pro-max" in line:
                ns_offenders.append(f"{p.relative_to(REPO_ROOT)}:{i}")
            if "skills/ui-ux-pro-max/" in line:
                path_offenders.append(f"{p.relative_to(REPO_ROOT)}:{i}")
    assert not ns_offenders, "stale hs:ui-ux-pro-max refs:\n" + "\n".join(ns_offenders)
    assert not path_offenders, "stale skills/ui-ux-pro-max/ runtime paths:\n" + "\n".join(path_offenders)
