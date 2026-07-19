"""skill_config.py — resolver for harness/data/skill-config.yaml (the human-edited plan +
per-skill knobs). Mirrors output_config's dual posture: load() fails OPEN (hook/skill path,
degrades a bad value to the default), load_strict() fails CLOSED (gate path, raises).
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import skill_config  # noqa: E402


def _write(tmp_path, text):
    p = tmp_path / "skill-config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_missing_fails_open_to_defaults(tmp_path):
    cfg = skill_config.load(str(tmp_path / "nope.yaml"))
    v = cfg["plan"]["validation"]
    assert v["mode"] == "prompt"
    assert v["minQuestions"] == 3
    assert v["maxQuestions"] == 8
    assert "assumptions" in v["focusAreas"]


def test_resolve_merges_three_namespaces(tmp_path):
    p = _write(tmp_path,
               "plan:\n  validation:\n    mode: auto\n    minQuestions: 5\n"
               "  resolution:\n    branchPattern: '(?:feat|fix)/(.+)'\n"
               "skills:\n  research:\n    useGemini: true\n")
    cfg = skill_config.load(p)
    assert cfg["plan"]["validation"]["mode"] == "auto"
    assert cfg["plan"]["validation"]["minQuestions"] == 5
    assert cfg["plan"]["validation"]["maxQuestions"] == 8  # default retained
    assert cfg["plan"]["resolution"]["branchPattern"] == "(?:feat|fix)/(.+)"
    assert cfg["skills"]["research"]["useGemini"] is True


def test_enum_reject_bad_mode_strict(tmp_path):
    p = _write(tmp_path, "plan:\n  validation:\n    mode: bogus\n")
    with pytest.raises(skill_config.SkillConfigError):
        skill_config.load_strict(p)


def test_bad_focus_area_strict(tmp_path):
    p = _write(tmp_path, "plan:\n  validation:\n    focusAreas: [assumptions, bogus]\n")
    with pytest.raises(skill_config.SkillConfigError):
        skill_config.load_strict(p)


def test_minmax_bounds_strict(tmp_path):
    p = _write(tmp_path, "plan:\n  validation:\n    maxQuestions: 99\n")
    with pytest.raises(skill_config.SkillConfigError):
        skill_config.load_strict(p)


def test_fail_open_keeps_default_on_bad_value(tmp_path):
    p = _write(tmp_path, "plan:\n  validation:\n    mode: bogus\n")
    cfg = skill_config.load(p)
    assert cfg["plan"]["validation"]["mode"] == "prompt"  # default preserved
    assert cfg.get("_diag"), "a bad value must be recorded in _diag on the fail-open path"


def test_skill_options_bag(tmp_path):
    p = _write(tmp_path, "skills:\n  research:\n    useGemini: true\n    depth: 3\n")
    opts = skill_config.skill_options("research", p)
    assert opts["useGemini"] is True and opts["depth"] == 3
    assert skill_config.skill_options("absent", p) == {}


def test_branch_pattern_extract_slug(tmp_path):
    p = _write(tmp_path, "plan:\n  resolution:\n    branchPattern: '(?:feat|fix)/(?:[^/]+/)?(.+)'\n")
    assert skill_config.extract_slug("feat/foo-bar", p) == "foo-bar"
    assert skill_config.extract_slug("feat/scope/foo-bar", p) == "foo-bar"
    assert skill_config.extract_slug("main", p) is None
