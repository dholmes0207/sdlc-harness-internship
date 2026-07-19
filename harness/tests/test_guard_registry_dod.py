"""test_guard_registry_dod.py — the DoD gate is an independently tunable guard.

test_policy_dod is registered as an ENFORCEMENT-category guard: it blocks under
strict + balanced and relaxes to warn under lenient (so a beginner repo gets a
nudge, a power repo gets a hard gate), with no safety floor (it is SDLC posture,
not system safety).
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import guard_policy as gp  # noqa: E402


def _policy(tmp_path, preset: str) -> Path:
    p = tmp_path / "guard-policy.yaml"
    p.write_text("schema_version: '1.0'\npreset: %s\noverrides: {}\n" % preset,
                 encoding="utf-8")
    return p


def test_registered_as_enforcement_no_floor():
    meta = gp.GUARD_REGISTRY["test_policy_dod"]
    assert meta["category"] == "enforcement"
    assert meta["floor"] is None


def test_blocks_under_strict_and_balanced(tmp_path):
    assert gp.resolve_mode("test_policy_dod", _policy(tmp_path, "strict")) == "block"
    assert gp.resolve_mode("test_policy_dod", _policy(tmp_path, "balanced")) == "block"


def test_warns_under_lenient(tmp_path):
    assert gp.resolve_mode("test_policy_dod", _policy(tmp_path, "lenient")) == "warn"


def test_blocks_under_solo(tmp_path):
    # solo keeps enforcement at block (the agent stays caged).
    assert gp.resolve_mode("test_policy_dod", _policy(tmp_path, "solo")) == "block"
