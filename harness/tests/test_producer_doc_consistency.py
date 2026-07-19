"""test_producer_doc_consistency.py — the producer docs must teach what the gate
accepts.

The verification-artifact reference once taught check names (`pytest-unit`,
`coverage-line`) the DoD gate rejects, and omitted the `format`/`file` the gate
re-derives from. These tests parse the documented example by `yaml.safe_load`
(NOT a line regex — flow-style `{name: unit}` and multiple examples make a regex
false-green) and assert every format-bearing example check names a canonical
test_type and carries a result file, and that SKILL.md mandates the validator.
"""
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import test_policy  # noqa: E402

_REF = (_ROOT / "plugins" / "hs" / "skills" / "test" / "references"
        / "verification-artifact.md")
_SKILL = _ROOT / "plugins" / "hs" / "skills" / "test" / "SKILL.md"


def _yaml_blocks(md_text):
    """Every ```yaml fenced block in the markdown, safe_loaded. Skips blocks that
    do not parse to a dict (templates with placeholders)."""
    out = []
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip().lower() == "```yaml":
            buf = []
            i += 1
            while i < len(lines) and lines[i].strip() != "```":
                buf.append(lines[i])
                i += 1
            try:
                doc = yaml.safe_load("\n".join(buf))
            except yaml.YAMLError:
                doc = None
            if isinstance(doc, dict):
                out.append(doc)
        i += 1
    return out


def _example_checks():
    """The `checks[]` from every verification example block in the reference."""
    blocks = _yaml_blocks(_REF.read_text(encoding="utf-8"))
    checks = []
    for b in blocks:
        if isinstance(b.get("checks"), list):
            checks.extend(c for c in b["checks"] if isinstance(c, dict))
    return checks


def _format_checks():
    return [c for c in _example_checks()
            if c.get("format") and c.get("format") != "manual"]


def _test_types():
    return test_policy.load_test_policy().get("test_types") or {}


# 1 — example demonstrates format-bearing checks, all with canonical names -----
def test_verification_example_uses_canonical_names():
    fmt_checks = _format_checks()
    assert fmt_checks, (
        "the verification example must demonstrate format-bearing checks — the "
        "DoD gate keys on format+file; a status-only example teaches a shape the "
        "gate cannot verify")
    types = _test_types()
    for c in fmt_checks:
        assert c.get("name") in types, (
            "example check name %r is not a canonical test_type %s"
            % (c.get("name"), sorted(types)))


# 2 — every format-bearing example check carries a result file ----------------
def test_verification_example_format_has_file():
    fmt_checks = _format_checks()
    assert fmt_checks
    for c in fmt_checks:
        assert c.get("file"), (
            "example check %r declares a format but no `file:` — the gate "
            "re-derives pass/fail from that file" % c.get("name"))


# 3 — SKILL.md mandates the producer-side validator ---------------------------
def test_skill_mandates_validator():
    assert "artifact_check.py --validate-verification" in _SKILL.read_text(encoding="utf-8"), (
        "test/SKILL.md must mandate `python3 harness/scripts/artifact_check.py --validate-verification` "
        "(the runnable script path, not a bare `-m artifact_check` module import) "
        "after writing the verification artifact")
