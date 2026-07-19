"""test_config_reference_output_knobs.py — config-reference.md documents the
output register knobs honestly after the re-home: an `audience` row, humanize
default OFF, code_style scoped to CODE only, and the --resolved discoverability line.
"""

import re
from pathlib import Path

RULES = Path(__file__).resolve().parents[1] / "rules"
CONFIG_REF = Path(__file__).resolve().parents[2] / "docs" / "harness" / "config-reference.md"


def _audience_row():
    for line in CONFIG_REF.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("| `audience`"):
            return line
    return None


def test_config_reference_has_audience_row():
    row = _audience_row()
    assert row is not None, "config-reference.md missing an `audience` knob row"
    assert "output.yaml" in row, "audience row must name harness/data/output.yaml"


def test_config_reference_humanize_default_off():
    text = CONFIG_REF.read_text(encoding="utf-8")
    hz = [l for l in text.splitlines() if l.lstrip().startswith("| `humanize`")]
    assert hz, "config-reference.md missing a `humanize` row"
    row = hz[0]
    # default column must read false/off, not true
    assert re.search(r"\b(false|off)\b", row, re.IGNORECASE), (
        "humanize row must document default false/off, got: %s" % row)
    assert not re.search(r"\|\s*true\s*\|", row), (
        "humanize row still documents default true: %s" % row)


def test_config_reference_code_style_is_code_only():
    text = CONFIG_REF.read_text(encoding="utf-8")
    cs = [l for l in text.splitlines() if l.lstrip().startswith("| `code_style`")]
    assert cs, "config-reference.md missing a `code_style` row"
    assert "prose AND code" not in cs[0], (
        "code_style row still claims prose AND code; it is CODE-only now")


def test_config_reference_has_resolved_discoverability():
    text = CONFIG_REF.read_text(encoding="utf-8")
    assert "--resolved" in text, (
        "config-reference.md missing the `output_config.py --resolved` one-command view")
