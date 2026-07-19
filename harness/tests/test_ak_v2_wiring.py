"""Wiring-barrier contract for the four ported skills.

The four new skill dirs must register into every shared registry consistently:
skill-deps (with REAL deps mirroring each body's namespaced routes + watzup
gaining handoff), the components/decomposition group pair, the ship default-off
list (three off, handoff on), and the STANDARDIZE port ledger. The count-test
bumps live in their own files; this test pins the registry shape.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "harness" / "data"

NEW = ["deep-swe", "folder-context", "handoff", "interview-docs"]
EXPECTED_DEPS = {
    "handoff": ["watzup"],
    "deep-swe": ["loop"],
    "folder-context": ["docs"],
    "interview-docs": ["brainstorm", "docs", "remember"],
}
# skill -> the group it belongs to; None means components-only (no decomposition entry).
GROUP = {
    "deep-swe": "research",
    "folder-context": "meta",
    "interview-docs": "mem",
    "handoff": "extra",  # components-only ck-port group, like watzup/prompt
}
DECOMPOSITION_GROUP = {"deep-swe": "research", "folder-context": "meta",
                       "interview-docs": "mem"}  # handoff excluded (extra not in decomposition-map)


def _load(name):
    return yaml.safe_load((DATA / name).read_text(encoding="utf-8"))


def test_new_skills_in_skill_deps():
    deps = _load("skill-deps.yaml")["skills"]
    for skill in NEW:
        assert skill in deps, "%s missing from skill-deps.yaml" % skill
        assert sorted(deps[skill].get("deps", [])) == sorted(EXPECTED_DEPS[skill]), \
            "%s deps %r != expected %r" % (skill, deps[skill].get("deps"), EXPECTED_DEPS[skill])
    assert "handoff" in deps["watzup"].get("deps", []), \
        "watzup.deps must gain handoff (it carries a prose hs:handoff route)"


def test_new_skills_group_consistency():
    comps = _load("components.yaml")["components"]
    decomp = _load("decomposition-map.yaml")["skills"]
    for skill, group in GROUP.items():
        assert skill in comps[group]["skills"], \
            "%s missing from components group %r" % (skill, group)
    for skill, group in DECOMPOSITION_GROUP.items():
        assert decomp.get(skill) == group, \
            "%s decomposition group %r != components %r" % (skill, decomp.get(skill), group)
    # handoff is components-only (extra), never in decomposition-map — like watzup.
    assert "handoff" not in decomp, \
        "handoff must not enter decomposition-map (extra is a components-only group)"


def test_ship_off_three():
    off = set(_load("skill-defaults.yaml")["default_off"])
    for skill in ("deep-swe", "folder-context", "interview-docs"):
        assert skill in off, "%s must ship default-off" % skill
    assert "handoff" not in off, "handoff ships ENABLED, must not be default-off"


def test_clusters_partition_still_holds():
    data = _load("skill-defaults.yaml")
    off = set(data["default_off"])
    clusters = data.get("clusters") or {}
    covered = set()
    for name, members in clusters.items():
        for m in members or []:
            assert m in off, "cluster %r lists %r not in default_off" % (name, m)
            assert m not in covered, "%r appears in two clusters" % m
            covered.add(m)
    assert covered == off, "clusters must partition default_off exactly; diff: %r" % (
        covered ^ off)
    assert len(off) == 78, "default_off should be 78 after the three ship-off skills"


def test_standardize_has_port_rows():
    text = (REPO_ROOT / "docs" / "STANDARDIZE.md").read_text(encoding="utf-8")
    for skill in NEW:
        assert skill in text, "STANDARDIZE.md missing a row for %s" % skill
    assert "skill-ecosystem-portability" in text, \
        "STANDARDIZE.md missing the skill-creator portability reference row"
    assert "docs/journals" not in text, \
        "STANDARDIZE.md journal rows must be updated to plans/journals"
