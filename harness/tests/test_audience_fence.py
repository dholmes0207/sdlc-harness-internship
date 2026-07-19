"""test_audience_fence.py — mechanical evidence-fence checker.

DETECTION only: checker compares two report texts and asserts evidence tokens
are identical. It does NOT prove LLM behaviour — only catches violations in
artifact pairs that are compared.
"""

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FENCE_SCRIPT = SCRIPTS / "audience_fence.py"

# Fixtures — shared evidence tokens
_EVIDENCE_TOKENS = [
    "harness/scripts/output_config.py:80",
    "DEC-123",
    "abc1234",
    "42",
    "`file:line`",
]

# Audience-0 fixture: long "so what" prose + evidence
_REPORT_A0 = """\
## What this means for you

This section explains what happened in plain language before showing the technical
details. The system ran a check and found some issues worth discussing. Let us walk
through the main points together so you understand the impact.

## Technical findings

The analysis ran against harness/scripts/output_config.py:80 and recorded decision
DEC-123. The commit reference is abc1234. The configuration has 42 keys. The key
pattern is `file:line`.

## Glossary

- DEC: Decision record in the project ledger.
- harness: The automation framework wrapping Claude Code.
"""

# Audience-5 fixture: terse, same evidence, no scaffolding
_REPORT_A5 = """\
harness/scripts/output_config.py:80 — DEC-123 registered, sha abc1234.
Config: 42 keys. Pattern: `file:line`.
"""

# Fixture where evidence is dropped (audience-0 missing a token)
_REPORT_MISSING_FILELINE = """\
## What this means

Plain version without the file reference. Decision DEC-123 was found.
Commit abc1234 is the anchor. There are 42 keys. Pattern is `file:line`.
"""

# Fixture where a quote is changed
_REPORT_QUOTE_CHANGED = """\
harness/scripts/output_config.py:80 — DEC-123, sha abc1234.
Keys: 42. Pattern: `file_line`.
"""

# Fixture where a number is changed
_REPORT_NUMBER_CHANGED = """\
harness/scripts/output_config.py:80 — DEC-123, sha abc1234.
Keys: 43. Pattern: `file:line`.
"""


def _run_fence(text_a: str, text_b: str, *, tmp_path=None):
    """Write fixtures to tmp files and invoke audience_fence.py; return (returncode, stdout+stderr)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        fa = Path(d) / "a.md"
        fb = Path(d) / "b.md"
        fa.write_text(text_a, encoding="utf-8")
        fb.write_text(text_b, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(FENCE_SCRIPT), str(fa), str(fb)],
            capture_output=True, text=True,
        )
        return result.returncode, result.stdout + result.stderr


def test_identical_evidence_passes():
    """Audience-0 (long) vs audience-5 (terse) — same evidence tokens → exit 0, empty diff."""
    rc, output = _run_fence(_REPORT_A0, _REPORT_A5)
    assert rc == 0, f"Expected exit 0 (identical evidence), got {rc}. Output:\n{output}"


def test_dropped_fileline_fails():
    """Report missing a file:line token → exit 1 + diff names the missing token."""
    # _REPORT_MISSING_FILELINE deliberately drops the file:line path anchor
    # (harness/scripts/output_config.py:80 is absent)
    _no_path = """\
## Plain version

Decision DEC-123. Commit abc1234. There are 42 keys. Pattern is `file:line`.
"""
    rc, output = _run_fence(_REPORT_A5, _no_path)
    assert rc == 1, f"Expected exit 1 (dropped token), got {rc}. Output:\n{output}"
    # The diff must name the missing token
    assert "output_config.py:80" in output or "harness" in output, (
        f"Diff does not identify dropped token. Output:\n{output}"
    )


def test_reworded_quote_fails():
    """A verbatim backtick token that is reworded (file:line -> file_line) → exit 1."""
    rc, output = _run_fence(_REPORT_A5, _REPORT_QUOTE_CHANGED)
    assert rc == 1, f"Expected exit 1 (reworded quote), got {rc}. Output:\n{output}"


def test_changed_number_fails():
    """A numeric value changed (42 -> 43) → exit 1."""
    rc, output = _run_fence(_REPORT_A5, _REPORT_NUMBER_CHANGED)
    assert rc == 1, f"Expected exit 1 (changed number), got {rc}. Output:\n{output}"


def test_extract_covers_token_classes():
    """extract_evidence() must capture all 5 token classes: file:line, DEC-ID, SHA, number, backtick."""
    sys.path.insert(0, str(SCRIPTS))
    import importlib, audience_fence
    importlib.reload(audience_fence)

    sample = (
        "See harness/scripts/output_config.py:80 and DEC-123 at commit abc1234ef. "
        "There are 42 entries. The key is `file:line`.\n"
        "```\nsome code block\n```"
    )
    tokens = audience_fence.extract_evidence(sample)

    # file:line
    assert any("output_config.py:80" in t for t in tokens), f"file:line not captured. Tokens: {tokens}"
    # DEC-ID
    assert any("DEC-123" in t for t in tokens), f"DEC-ID not captured. Tokens: {tokens}"
    # SHA
    assert any("abc1234" in t for t in tokens), f"SHA not captured. Tokens: {tokens}"
    # number
    assert any("42" in t for t in tokens), f"Number not captured. Tokens: {tokens}"
    # backtick token
    assert any("file:line" in t for t in tokens), f"backtick token not captured. Tokens: {tokens}"
