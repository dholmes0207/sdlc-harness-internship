"""SKILL.md thin-core, frontmatter, de-brand, directives backed.

Tests run against the live drawio skill directory. They fail until the SKILL.md
is written and references de-branded.
"""
import re
import pytest
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAWIO_SKILL = REPO_ROOT / "harness" / "plugins" / "hs" / "skills" / "drawio"
SCRIPTS_DIR = DRAWIO_SKILL / "scripts"
SKILL_MD = DRAWIO_SKILL / "SKILL.md"
EDIT_MODE_MD = DRAWIO_SKILL / "references" / "edit-mode.md"
CHECK_SCRIPT = REPO_ROOT / "harness" / "scripts" / "check_skill_structure.py"

# Frontmatter fields that must NOT appear (upstream-only, plus the S2-retired
# harness fields: category/keywords/user-invocable have no consumer and cost
# listing budget for nothing).
FORBIDDEN_FRONTMATTER = {"version", "license", "homepage", "compatibility", "hermes", "openclaw",
                        "category", "keywords", "user-invocable"}



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a SKILL.md file."""
    import yaml
    text = path.read_text()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    return yaml.safe_load(m.group(1)) or {}


def test_skill_structure_passes():
    """check_skill_structure.py must report no blocking (non-grandfathered HARD) finding
    for the drawio skill dir. Run in the default sweep view (not --strict): drawio carries
    pre-existing thin-core debt grandfathered in harness/data/thin-core-grandfather.yaml,
    which CI --strict deliberately re-surfaces on the next edit."""
    import json
    assert SKILL_MD.exists(), "SKILL.md not found - need to write it"
    result = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), str(DRAWIO_SKILL)],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"check_skill_structure errored:\n"
        f"{result.stdout.decode()}\n{result.stderr.decode()}"
    )
    assert json.loads(result.stdout.decode())["hard"] == 0, (
        f"drawio gained a non-grandfathered HARD finding:\n{result.stdout.decode()}"
    )


def test_frontmatter_fields():
    """SKILL.md must have the correct harness frontmatter fields."""
    assert SKILL_MD.exists(), "SKILL.md not found"
    fm = _parse_frontmatter(SKILL_MD)
    assert fm.get("name") == "hs:drawio", f"name should be 'hs:drawio', got {fm.get('name')!r}"
    meta = fm.get("metadata") or {}
    assert meta.get("compliance-tier") == "workflow", (
        f"compliance-tier must be 'workflow', got {meta.get('compliance-tier')!r}"
    )
    # Forbidden upstream-only fields
    for field in FORBIDDEN_FRONTMATTER:
        assert field not in fm, f"Forbidden frontmatter field {field!r} found"


def test_no_this_skill_dir_placeholder():
    """No file in skill dir may contain <this-skill-dir> placeholder."""
    assert DRAWIO_SKILL.exists()
    violations = []
    for path in DRAWIO_SKILL.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "<this-skill-dir>" in line:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{i}: {line.rstrip()!r}")
    assert not violations, (
        "Found <this-skill-dir> placeholders:\n" + "\n".join(violations)
    )


def test_directives_backed():
    """Every scripts/X.py path referenced in SKILL.md body must exist."""
    assert SKILL_MD.exists()
    body = SKILL_MD.read_text()
    # Strip frontmatter
    m = re.match(r"^---\s*\n.*?\n---\s*\n", body, re.DOTALL)
    if m:
        body = body[m.end():]

    # Find all scripts/X.py references
    refs = re.findall(r"scripts/([A-Za-z0-9_]+\.py)", body)
    missing = []
    for name in set(refs):
        script = SCRIPTS_DIR / name
        if not script.exists():
            missing.append(f"scripts/{name}")
    assert not missing, (
        "SKILL.md references scripts that don't exist:\n" + "\n".join(missing)
    )


def test_edit_mode_reference_exists_and_linked():
    """The incremental-edit reference must exist and be linked from SKILL.md
    (not an orphan), and SKILL.md must stay within thin-core (≤200 body lines,
    enforced by check_skill_structure in test_skill_structure_passes)."""
    assert EDIT_MODE_MD.exists(), "references/edit-mode.md not found"
    body = SKILL_MD.read_text()
    assert "references/edit-mode.md" in body, (
        "SKILL.md must link references/edit-mode.md"
    )


def test_edit_mode_contract_keys():
    """edit-mode.md must spell out the ops contract an agent has to follow."""
    assert EDIT_MODE_MD.exists()
    text = EDIT_MODE_MD.read_text()
    for key in ('"operation"', '"cell_id"', "--list-cells", "--faithful", "validate.py"):
        assert key in text, f"edit-mode.md missing contract key {key!r}"


def test_no_dev_id_labels():
    """SKILL.md must not contain DEC-N, F-N, or plan-id labels."""
    assert SKILL_MD.exists()
    text = SKILL_MD.read_text()
    violations = []
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(r"\bDEC-\d+\b", line):
            violations.append(f"line {i}: DEC-N label: {line.rstrip()!r}")
        if re.search(r"\bF-\d+\b", line):
            violations.append(f"line {i}: F-N label: {line.rstrip()!r}")
        if "plan-id" in line.lower():
            violations.append(f"line {i}: plan-id label: {line.rstrip()!r}")
    assert not violations, (
        "SKILL.md contains dev-id labels:\n" + "\n".join(violations)
    )


ENGLISH_SECTION_HEADINGS = [
    "## When to use",
    "## Prerequisites",
    "## Workflow",
    "## References",
    "## Boundaries",
]


def test_english_section_headings_present():
    """SKILL.md body must contain key English section headings.

    Checks presence of expected English headings — not a blocklist
    of banned words (red-team M3: blocklist fragile across translations).
    """
    assert SKILL_MD.exists(), "SKILL.md not found"
    text = SKILL_MD.read_text()
    missing = []
    for heading in ENGLISH_SECTION_HEADINGS:
        if heading not in text:
            missing.append(heading)
    assert not missing, (
        "SKILL.md body missing English section headings:\n"
        + "\n".join(missing)
    )
