"""test_portback_p4.py — restored CI-convergence loops + workflow steps.

These prose ports were compressed to one-liners during the original port. The
suite locks the load-bearing markers back in: the merge-pr CI-watch loop, the
review-pr conflict-resolution gate, the ship Link-Issues + Human-Review-Tasks
PR template, the bootstrap --parallel design phase, and the preview analysis
views. Re-thinning any of these trips a marker assertion here.
"""
import re
from pathlib import Path

import pytest  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]
_SKILLS = _REPO / "harness" / "plugins" / "hs" / "skills"

_NEW = [
    _SKILLS / "bootstrap" / "references" / "workflow-parallel.md",
    _SKILLS / "preview" / "references" / "analysis-diff.md",
    _SKILLS / "preview" / "references" / "analysis-plan-review.md",
    _SKILLS / "preview" / "references" / "analysis-recap.md",
]


@pytest.mark.dev_repo
def test_new_files_exist():
    missing = [str(p.relative_to(_REPO)) for p in _NEW if not p.exists()]
    assert not missing, "missing:\n" + "\n".join(missing)


def test_merge_pr_has_ci_watch_loop():
    body = (_SKILLS / "git" / "references" / "workflow-merge-pr.md").read_text(encoding="utf-8")
    assert "gh run watch" in body, "merge-pr CI-watch loop (Step 5/6) missing"


def test_review_pr_fix_has_conflict_resolution():
    body = (_SKILLS / "review-pr" / "SKILL.md").read_text(encoding="utf-8")
    assert "mergeStateStatus" in body, "review-pr --fix conflict-resolution marker missing"


def test_ship_pr_template_has_link_issues_and_human_review():
    body = (_SKILLS / "ship" / "references" / "gate-sequence.md").read_text(encoding="utf-8")
    assert "Linked Issues" in body, "ship PR template missing Linked Issues block"
    assert "Human Review Tasks" in body, "ship PR template missing Human Review Tasks checklist"


@pytest.mark.dev_repo
def test_bootstrap_argument_hint_has_parallel():
    body = (_SKILLS / "bootstrap" / "SKILL.md").read_text(encoding="utf-8")
    assert "--parallel" in body, "bootstrap argument-hint missing --parallel"


@pytest.mark.dev_repo
def test_preview_argument_hint_has_analysis_flags():
    body = (_SKILLS / "preview" / "SKILL.md").read_text(encoding="utf-8")
    for flag in ["--plan-review", "--diff", "--recap"]:
        assert flag in body, f"preview missing {flag}"


def test_ported_set_rebranded_clean():
    bad = re.compile(r"/?\bck:")
    offenders = []
    for p in _NEW:
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if bad.search(line) and not line.lstrip().startswith("# learn:"):
                offenders.append(f"{p.relative_to(_REPO)}:{i}: {line.strip()[:80]}")
    assert not offenders, "ck: brand survived:\n" + "\n".join(offenders)
