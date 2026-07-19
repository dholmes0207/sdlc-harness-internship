#!/usr/bin/env python3
"""verify_install must catch a component that declares a file absent on disk —
a shipped component with a dangling member would wire/enable a missing file."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import verify_install as vi  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent.parent


def test_real_tree_components_clean():
    # every file the shipped components.yaml declares must exist
    assert vi.component_file_problems(_ROOT) == []


def test_flags_missing_declared_hook(tmp_path):
    (tmp_path / "harness" / "data").mkdir(parents=True)
    (tmp_path / "harness" / "data" / "components.yaml").write_text(
        "components:\n"
        "  ghost:\n"
        "    hooks: [does_not_exist]\n"
        "    requires: []\n", encoding="utf-8")
    probs = vi.component_file_problems(tmp_path)
    assert any("does_not_exist" in rel for rel, _ in probs)


def test_flags_missing_declared_script_and_data(tmp_path):
    (tmp_path / "harness" / "data").mkdir(parents=True)
    (tmp_path / "harness" / "data" / "components.yaml").write_text(
        "components:\n"
        "  ghost:\n"
        "    scripts: [no_such_script]\n"
        "    data: [no_such.yaml]\n"
        "    requires: []\n", encoding="utf-8")
    probs = vi.component_file_problems(tmp_path)
    rels = [rel for rel, _ in probs]
    assert any("no_such_script.py" in r for r in rels)
    assert any("no_such.yaml" in r for r in rels)


def test_skills_are_not_checked(tmp_path):
    # a skill may be a not-yet-ported placeholder → informational, never a drift
    (tmp_path / "harness" / "data").mkdir(parents=True)
    (tmp_path / "harness" / "data" / "components.yaml").write_text(
        "components:\n"
        "  ghost:\n"
        "    skills: [unported_skill]\n"
        "    requires: []\n", encoding="utf-8")
    assert vi.component_file_problems(tmp_path) == []


def test_empty_without_components_file(tmp_path):
    assert vi.component_file_problems(tmp_path) == []
