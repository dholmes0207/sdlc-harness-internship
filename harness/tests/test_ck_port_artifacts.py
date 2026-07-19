"""test_ck_port_artifacts.py — two MIT prose artifacts ported into the harness.

Covers the agent-level post-failure capture file (harness/LESSONS.md) and the
agent-operational-discipline rule. Both are prose ports (rebranded, self-
contained). The tests lock four things:

  1. both files exist and carry real content (not a stub);
  2. neither leaks an un-prefixed runtime path string (the CI fence allows such
     a string only on a `# learn:` citation line);
  3. STANDARDIZE.md gained one provenance row per ported file, each citing the
     dest path and the ClaudeKit/MIT attribution;
  4. the two preflight skills (understand, plan) point at harness/LESSONS.md so
     the capture file is actually read.
"""
from pathlib import Path

import pytest  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]

_LESSONS = _REPO / "harness" / "LESSONS.md"
_DISCIPLINE = _REPO / "harness" / "rules" / "agent-operational-discipline.md"
_STANDARDIZE = _REPO / "docs" / "STANDARDIZE.md"
_UNDERSTAND = _REPO / "harness" / "plugins" / "hs" / "skills" / "understand" / "SKILL.md"
_PLAN = _REPO / "harness" / "plugins" / "hs" / "skills" / "plan" / "SKILL.md"

# The banned literals are assembled at runtime so THIS test file does not itself
# contain them (the cross-cutting invariant scans every text file under harness/).
_DOT = "." + "claude"
_PATH_LITERALS = ("%s/%s/" % (_DOT, "skills"), "%s/%s/" % (_DOT, "hooks"))


def _unfenced_path_hits(text):
    """Lines carrying a runtime path literal that are NOT a `# learn:` citation."""
    hits = []
    for line in text.splitlines():
        if line.lstrip().startswith("# learn:"):
            continue
        for lit in _PATH_LITERALS:
            if lit in line:
                hits.append(line)
    return hits


def test_both_ported_files_exist_and_are_nontrivial():
    for path in (_LESSONS, _DISCIPLINE):
        assert path.is_file(), "missing ported file: %s" % path
        body = path.read_text(encoding="utf-8").strip()
        assert len(body) > 200, "ported file too thin: %s" % path
        assert body.lstrip().startswith("#"), \
            "ported file should be markdown with a heading: %s" % path


def test_no_unfenced_runtime_path_strings():
    for path in (_LESSONS, _DISCIPLINE):
        hits = _unfenced_path_hits(path.read_text(encoding="utf-8"))
        assert hits == [], \
            "%s leaks an un-prefixed runtime path string: %s" % (path, hits)


def test_ported_files_carry_no_claudekit_brand():
    for path in (_LESSONS, _DISCIPLINE):
        text = path.read_text(encoding="utf-8").lower()
        assert "claudekit" not in text, "%s still names ClaudeKit" % path
        assert "ck_" not in text, "%s still uses a CK_ env prefix" % path


@pytest.mark.dev_repo  # reads docs/STANDARDIZE.md — dev-only, absent on installs
def test_standardize_gained_two_rows_for_the_ports():
    text = _STANDARDIZE.read_text(encoding="utf-8")
    assert "harness/LESSONS.md" in text, \
        "STANDARDIZE.md lacks a provenance row for harness/LESSONS.md"
    assert "harness/rules/agent-operational-discipline.md" in text, \
        "STANDARDIZE.md lacks a provenance row for the discipline rule"
    # each provenance row must carry the MIT attribution
    rows = [ln for ln in text.splitlines()
            if "agent-operational-discipline.md" in ln or "harness/LESSONS.md" in ln]
    attributed = [ln for ln in rows if "(ClaudeKit, MIT)" in ln]
    assert len(attributed) >= 2, \
        "both port rows must carry the (ClaudeKit, MIT) attribution"


def test_preflight_skills_reference_lessons():
    for path in (_UNDERSTAND, _PLAN):
        text = path.read_text(encoding="utf-8")
        assert "harness/LESSONS.md" in text, \
            "%s does not reference harness/LESSONS.md as preflight input" % path


def test_primary_workflow_references_discipline_rule():
    text = (_REPO / "harness" / "rules" / "primary-workflow.md").read_text(encoding="utf-8")
    assert "agent-operational-discipline.md" in text, \
        "primary-workflow.md does not surface the discipline rule"
