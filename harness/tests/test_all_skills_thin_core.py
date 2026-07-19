"""Every shipped skill stays thin-core — a repo-wide backstop for the size cap.

`check_skill_structure.py` enforces a HARD ceiling (SKILL.md body <= 200 lines,
each reference <= 300, no broken reference link, no birth-marker leak). But the
ci_local.sh invocation only runs `--strict` on skills CHANGED vs main:

    git diff --name-only main -- harness/plugins | ... | check_skill_structure --strict

So a violation on an UNCHANGED skill is never re-checked and slips through forever
(this is exactly how `setup` carried a 234-line body as grandfathered debt). This
sweep closes that gap: it checks EVERY skill on every test run, independent of the
diff, so a grandfathered or newly-bloated skill reddens the suite.

The hard gate is structural only; description-shape / orphan-reference / PII findings
stay ADVISORY and do not fail here (there are intentionally many).
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_SCRIPT = REPO_ROOT / "harness" / "scripts" / "check_skill_structure.py"
SKILLS_ROOT = REPO_ROOT / "harness" / "plugins" / "hs" / "skills"


def _sweep(path: Path) -> dict:
    """Run check_skill_structure over a root and return the aggregate JSON."""
    r = subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), str(path)],
        capture_output=True, text=True, timeout=60,
    )
    assert r.stdout, "check_skill_structure produced no stdout:\n%s" % r.stderr
    return json.loads(r.stdout)


def test_no_shipped_skill_has_a_hard_finding():
    """No skill or agent may carry a HARD structure finding (over-budget body/reference/
    agent, over-long line, broken reference link, birth-marker leak). Pre-existing debt
    from the line->char standard migration is grandfathered inside the checker itself
    (downgraded to advisory in this default sweep view; CI --strict re-teeths it on the
    next edit — see harness/data/thin-core-grandfather.yaml). Lists offenders on failure.
    """
    if not SKILLS_ROOT.is_dir():
        return  # an install that omitted all skills has nothing to sweep
    result = _sweep(SKILLS_ROOT)
    offenders = []
    for entry in result.get("skills", []) + result.get("agents", []):
        hard = [f["detail"] for f in entry.get("findings", []) if f["severity"] == "hard"]
        if hard:
            offenders.append((entry.get("skill") or entry.get("agent"), hard))
    assert not offenders, (
        "skills/agents with HARD structure findings — split detail into references/:\n"
        + "\n".join(
            "  %s:\n    %s" % (name, "\n    ".join(details))
            for name, details in offenders
        )
    )


def test_sweep_has_teeth_on_an_over_cap_skill(tmp_path):
    """Guard the guard: a synthetic skill whose body exceeds the CHAR cap MUST fail the
    sweep — proving the assertion above is not vacuously green."""
    d = tmp_path / "bloated"
    d.mkdir()
    # Many short lines whose TOTAL chars blow the body budget (short lines, so this
    # trips the size cap alone, not the per-line cap).
    body = "\n".join("filler line %d" % i for i in range(1600))
    (d / "SKILL.md").write_text(
        "---\nname: bloated\ndescription: deliberately over the body cap for the teeth test\n---\n"
        + body,
        encoding="utf-8",
    )
    result = _sweep(tmp_path)
    assert result["verdict"] == "FAIL", result
    assert result["hard"] >= 1, result
    details = [
        f["detail"]
        for s in result["skills"]
        for f in s["findings"]
        if f["severity"] == "hard"
    ]
    assert any("body is" in d and "chars (max" in d for d in details), details
