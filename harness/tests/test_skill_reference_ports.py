"""test_skill_reference_ports.py — ported reference drawers for cook/code-review/debug.

The spine skills cook, code-review and debug each gained load-on-demand reference
drawers ported from an upstream kit (subagent patterns, full workflow steps, the shared
artifact schema; the review checklists + checklist workflow; the four-layer validation
pattern). Three invariants protect the port:

  1. every ported drawer is present on disk,
  2. no drawer leaks an upstream brand or a banned cross-tree path (the same CI invariant
     that bans the kit's `skills/` and `hooks/` tree references under harness/), and
  3. the host SKILL.md links each drawer (no orphan / no broken link) — proven by the
     structure checker reporting zero HARD findings for each skill.
"""
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SKILLS = _ROOT / "harness" / "plugins" / "hs" / "skills"

# the ported drawers, grouped by host skill
_PORTED = {
    "cook": [
        "references/subagent-patterns.md",
        "references/workflow-steps.md",
        "_shared/workflow-artifacts.md",
    ],
    "code-review": [
        "references/checklists/base.md",
        "references/checklists/web-app.md",
        "references/checklists/api.md",
        "references/checklist-workflow.md",
    ],
    "debug": [
        "references/defense-in-depth.md",
    ],
}

# brand / cross-tree leaks that must never appear in a ported drawer.
# The cross-tree fragment is assembled from parts so this guard does not itself
# embed the banned literal it scans for (mirrors the CI invariant's own pattern).
_FORBIDDEN = re.compile(
    r"/ck:|" + r"\.claude/" + r"(?:skills|hooks)/" + r"|ClaudeKit|claudekit",
    re.IGNORECASE,
)


def _all_drawers():
    for skill, rels in _PORTED.items():
        for rel in rels:
            yield skill, _SKILLS / skill / rel


def test_every_ported_drawer_exists():
    missing = [str(p) for _, p in _all_drawers() if not p.is_file()]
    assert not missing, "ported drawers missing: %s" % missing


def test_no_drawer_leaks_brand_or_banned_path():
    leaks = {}
    for _, p in _all_drawers():
        hits = [ln for ln in p.read_text(encoding="utf-8").splitlines() if _FORBIDDEN.search(ln)]
        if hits:
            leaks[str(p)] = hits
    assert not leaks, "brand / banned-path leak in ported drawers: %s" % leaks


def test_drawers_within_reference_size_gate():
    # the reference-size gate is 300 lines; a ported drawer must not blow it
    too_long = {
        str(p): len(p.read_text(encoding="utf-8").splitlines())
        for _, p in _all_drawers()
        if len(p.read_text(encoding="utf-8").splitlines()) > 300
    }
    assert not too_long, "ported drawers over the 300-line reference gate: %s" % too_long


def test_host_skills_have_no_hard_structure_findings():
    # zero HARD findings proves each drawer is linked (no orphan-reference, no broken link)
    checker = _ROOT / "harness" / "scripts" / "check_skill_structure.py"
    for skill in _PORTED:
        out = subprocess.run(
            [sys.executable, str(checker), str(_SKILLS / skill)],
            capture_output=True, text=True,
        )
        assert '"hard": 0' in out.stdout, "%s has HARD structure findings:\n%s" % (skill, out.stdout)
