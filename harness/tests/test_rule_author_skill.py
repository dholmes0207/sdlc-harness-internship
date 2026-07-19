"""Tests for the hs:rule-author skill (P7) — authoring standards.user.yaml.

The skill is registered (catalog + skill-deps), thin-core (passes
check_skill_structure), and its documented output format round-trips through the
P6 override loader. Authoring is LLM-time; review-time reads the static file.
"""

import subprocess
import sys
from pathlib import Path

import yaml as _yaml

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_SKILL_DIR = _REPO / "harness" / "plugins" / "hs" / "skills" / "rule-author"


def test_skill_rule_author_structure():
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_skill_structure.py"), str(_SKILL_DIR)],
        capture_output=True, text=True)
    assert '"hard": 0' in out.stdout, out.stdout
    assert '"verdict": "PASS"' in out.stdout, out.stdout


def test_skill_frontmatter_name_namespaced():
    text = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    fm = _yaml.safe_load(text.split("---", 2)[1])
    assert fm["name"] == "hs:rule-author"  # hs:-prefixed (invocation follows frontmatter name)


def test_skill_deps_has_rule_author():
    import skill_deps
    data = skill_deps.load_deps(_REPO / "harness" / "data" / "skill-deps.yaml")
    assert "rule-author" in data["skills"]


def test_authored_user_yaml_valid(tmp_path, monkeypatch):
    # a sample standards.user.yaml in the skill's documented format loads + applies
    sample = {
        "overrides": [
            {"rule_id": "STD-REVIEW-PY-RG1-R1", "reason": "task boundary uses bare except",
             "severity": "info"},
            {"rule_id": "USER-HANDLERS-STRICT", "reason": "no request bodies in logs",
             "scope": ["src/handlers/**/*.py"], "severity": "critical"},
        ]
    }
    p = tmp_path / "standards.user.yaml"
    p.write_text(_yaml.safe_dump(sample), encoding="utf-8")
    monkeypatch.setenv("HARNESS_USER_OVERRIDE", str(p))

    import user_override
    overrides = user_override.load(tmp_path)
    assert len(overrides) == 2
    rules = [{"id": "STD-REVIEW-PY-RG1-R1", "type": "rule", "scope": ["**/*.py"],
              "severity": "critical", "floor": False, "enabled": True,
              "relates_to_std": []}]
    out, warnings = user_override.apply(rules, overrides)
    ids = {r["id"] for r in out}
    assert "USER-HANDLERS-STRICT" in ids                      # new rule added
    r1 = next(r for r in out if r["id"] == "STD-REVIEW-PY-RG1-R1")
    assert r1["severity"] == "info"                           # override applied
    assert warnings                                           # loud by design
