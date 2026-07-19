"""Structural guards for the contract-validation skill (hs:contract-test).

These read always-present data files (stage-policy.yaml, skill-deps.yaml), so they
run on installed copies too — unlike the prose-presence greps for the skill body,
which live in the dev_repo-marked prose suite.

The load-bearing guard is `not_in_stage_requires`: the probe must never sit in a
stage's `requires:` (that would make it gate-driven — every push would execute target
code, an RCE trigger). NOTE (R6): this proves the *structural* non-gate-driven property
only. It does NOT prove the AI-text->shell injection path is closed — that lives in the
probe-catalog review discipline, not here.
"""
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import skill_deps  # noqa: E402

_NAMES = ("contract-test", "contract-validation")


def test_contract_validation_not_in_stage_requires():
    policy = yaml.safe_load(
        (_ROOT / "harness" / "data" / "stage-policy.yaml").read_text(encoding="utf-8"))
    stages = policy.get("stages", {})
    for stage, cfg in stages.items():
        req = cfg.get("requires", []) or []
        for name in _NAMES:
            assert name not in req, (
                "%s must NOT be in stage %r requires: (gate-driven probe = RCE trigger)"
                % (name, stage))


def test_contract_test_registered_with_manual_test_dep():
    data = skill_deps.load_deps(_ROOT / "harness" / "data" / "skill-deps.yaml")
    assert "contract-test" in data["skills"], "contract-test missing from skill-deps.yaml"
    assert "manual-test" in data["skills"]["contract-test"]["deps"]
