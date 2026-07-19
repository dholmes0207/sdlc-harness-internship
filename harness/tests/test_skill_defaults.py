"""Lock the shipped default-off skill catalog (skill-defaults.yaml).

A fresh install ships ~38 skills ON and 71 OFF (copied to the stash, reachable via
the hs:use proxy). This SSOT is hand-edited YAML; these tests pin the invariants the
list must never silently drift from: it may never hide a floor or an interview-kept
skill, every name must be a real skill, the count is fixed, and the onboarding
clusters must partition the OFF set exactly.
"""
import sys
import pytest
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import skill_deps  # noqa: E402
import skill_selection  # noqa: E402

_DEFAULTS = _REPO / "harness" / "data" / "skill-defaults.yaml"
_DEPS = _REPO / "harness" / "data" / "skill-deps.yaml"

# The interview keep-list (#21): skills that stay ON at ship even though usage is thin,
# because the PO judged them load-bearing for the assistant's reasoning / QA surface.
_KEEP_ON = {
    "scenario", "manual-test", "techstack",                       # QA-extras
    "discover", "brainstorm", "critique", "problem-solving",  # brain
    "remember", "rule-author", "docs",
    # live spine deps that must not be omitted out from under an ON spine skill
    "sequential-thinking", "workflow-orchestrate", "research", "bakeoff", "loop",
    "predict", "worktree", "context-engineering", "security-scan", "afk", "voice",
}
_OFF_COUNT = 78


def _load():
    return yaml.safe_load(_DEFAULTS.read_text(encoding="utf-8"))


def _off():
    return list(_load().get("default_off") or [])


def test_default_off_disjoint_from_floor():
    floor = set(skill_deps.core_immutable(_DEPS))
    assert set(_off()) & floor == set(), "default_off must never hide a floor skill"


@pytest.mark.dev_repo
def test_default_off_names_are_real_skills():
    real = skill_selection.all_skills(_REPO)
    bogus = [s for s in _off() if s not in real]
    assert bogus == [], "default_off names not present as real skills: %r" % bogus


def test_default_off_excludes_interview_keep_list():
    bad = sorted(set(_off()) & _KEEP_ON)
    assert bad == [], "default_off must not contain interview-kept skills: %r" % bad


def test_counts_add_up():
    off = _off()
    assert len(off) == len(set(off)), "default_off has duplicates"
    assert len(off) == _OFF_COUNT, "default_off count drifted from %d: got %d" % (
        _OFF_COUNT, len(off))


def test_clusters_partition_off_list():
    data = _load()
    off = set(_off())
    clusters = data.get("clusters") or {}
    seen = {}
    for name, members in clusters.items():
        for m in members or []:
            assert m in off, "cluster %r lists %r which is not in default_off" % (name, m)
            assert m not in seen, "%r appears in two clusters: %r and %r" % (
                m, seen[m], name)
            seen[m] = name
    assert set(seen) == off, "clusters must cover every OFF skill; missing: %r" % (
        sorted(off - set(seen)))
