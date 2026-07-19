"""test_report_reads_resolved.py — convention enforcer (ex-BL-114).

Scans the 21 report skills/agents and fails if any directs the model to read the
register knobs (audience / humanize / language) from the RAW tracked output.yaml
or a bare `output_config.py` invocation instead of via `--resolved`. The raw file
is the fail-closed gate path and ignores the dev override, so a report rendered
off it returns the wrong register for a developer who set a knob locally — and a
subagent reading raw gets stale values it could never override.

Catches both drift in the existing stanzas and a NEW report skill/agent added
later that wires the wrong source. Pattern mirrors the repo's other pytest
enforcers (mirror-drift, handoff-deps-drift).
"""

import re
import pytest
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills"
AGENTS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "agents"

REPORT_SKILLS = [
    "code-review", "docs", "setup", "brainstorm", "plan", "research", "insights",
    "bakeoff", "remember", "critique", "compound", "discover", "agentize",
]
REPORT_AGENTS = [
    "code-reviewer", "planner", "researcher", "debugger", "docs-manager",
    "brainstormer", "journal-writer", "critique-consolidator",
]

# A line that reads/follows output.yaml as a SOURCE — the raw-read tell. The
# canonical pointer says "do NOT hand-read the tracked config file" and never
# names output.yaml, so it does not trip this.
_RAW_READ = re.compile(r"(read|reads|follows)\b[^\n]*output\.yaml", re.IGNORECASE)



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _report_files():
    for s in REPORT_SKILLS:
        yield ("skill:" + s, SKILLS_DIR / s / "SKILL.md")
    for a in REPORT_AGENTS:
        yield ("agent:" + a, AGENTS_DIR / (a + ".md"))


def test_no_report_file_reads_raw_output_yaml():
    """No report stanza may instruct reading the register from raw output.yaml."""
    offenders = []
    for label, path in _report_files():
        assert path.exists(), f"report file not found: {label} ({path})"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "--resolved" in line:
                continue  # the sanctioned resolver line is allowed
            if _RAW_READ.search(line):
                offenders.append(f"{label}:{i}: {line.strip()[:100]}")
    assert not offenders, (
        "Report files reading the register from RAW output.yaml (use "
        "`output_config.py --resolved`):\n  " + "\n  ".join(offenders))


def test_every_report_file_invokes_resolved():
    """Each report file must invoke the resolver (`--resolved`) — the positive
    half: a new skill that forgot the resolver fails here."""
    missing = [label for label, path in _report_files()
               if "--resolved" not in path.read_text(encoding="utf-8")]
    assert not missing, (
        "Report files missing the `--resolved` resolver invocation:\n  "
        + "\n  ".join(missing))


_REGISTER_WORD = re.compile(r"\b(audience|humanize|language)\b", re.IGNORECASE)


def test_output_config_register_read_uses_resolved():
    """An `output_config.py` invocation that reads a REGISTER knob (audience /
    humanize / language) for rendering must use `--resolved` (or be a `--set`
    write). A generic config-read form (e.g. setup documenting `--file`) that does
    not name a register knob is left alone."""
    offenders = []
    for label, path in _report_files():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "output_config.py" not in line:
                continue
            if "--resolved" in line or "--set" in line:
                continue
            if _REGISTER_WORD.search(line):
                offenders.append(f"{label}:{i}: {line.strip()[:100]}")
    assert not offenders, (
        "Register-knob read via bare `output_config.py` in a report file (use "
        "--resolved):\n  " + "\n  ".join(offenders))
