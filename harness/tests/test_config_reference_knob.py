"""test_config_reference_knob.py — the check-name validation knob is documented.

A knob that nobody can find is a knob that rots. config-reference.md is the
single index of every tunable; this pins that check_name_validation is listed
there with its file and its three ramp values.
"""
from pathlib import Path

_REF = Path(__file__).resolve().parents[2] / "docs" / "harness" / "config-reference.md"


def test_check_name_validation_knob_documented():
    text = _REF.read_text(encoding="utf-8")
    assert "check_name_validation" in text
    assert "test-policy.yaml" in text
    for ramp in ("off", "soft", "hard"):
        assert ramp in text
