"""injectable frontmatter field rollout (phase 2).

Three parts:
  (a) bootstrap classifier — spine_false → false, everything else → true; covers
      disabled/stashed skills; never writes without an explicit --apply.
  (b) validator tripwire in check_skill_structure — injectable:true + a gate CALL
      in the BODY (a python3 harness/{hooks,scripts}/<gate>.py to a basename in the
      closed enum) is HARD. A bare prose mention (F1) or a non-gate call (F-D) is
      not flagged; a call in the FRONTMATTER is not flagged (F8, body-only).
  (c) is_injectable — fail-closed: an ABSENT field is NOT injectable (F-F).
"""
import re
import sys
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent.parent
for _p in (_HARNESS / "scripts",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import check_skill_structure as css  # noqa: E402
import injectable_bootstrap as ib  # noqa: E402

_REAL_SKILLS = _HARNESS / "plugins" / "hs" / "skills"


def _write_skill(dir_, frontmatter, body):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(
        "---\n%s---\n%s" % (frontmatter, body), encoding="utf-8")
    return dir_


def _real_body(name):
    text = (_REAL_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", text, re.DOTALL)
    return m.group(1) if m else text


def _copy_real_with_injectable(tmp, name, value):
    """Copy a real skill's SKILL.md into tmp with `injectable: <value>` set."""
    d = tmp / name
    _write_skill(d, "name: hs:%s\ninjectable: %s\n" % (name, str(value).lower()),
                 _real_body(name))
    return d


# --- (b) validator tripwire -------------------------------------------------
def test_validator_flags_gate_call_conflict(tmp_path):
    d = _write_skill(tmp_path / "faux",
                     "name: hs:faux\ninjectable: true\n",
                     "# Faux\n\n```bash\npython3 harness/hooks/gate_stage.py\n```\n")
    res = css.check_skill(str(d))
    rules = [f["rule"] for f in res["findings"]]
    assert "injectable-gate-conflict" in rules
    assert res["verdict"] == "FAIL"


def test_validator_passes_prose_mention(tmp_path):
    # a real advisory skill that MENTIONS a gate in prose but never calls it
    d = _copy_real_with_injectable(tmp_path, "sequential-thinking", True)
    res = css.check_skill(str(d))
    assert "injectable-gate-conflict" not in [f["rule"] for f in res["findings"]]


@pytest.mark.parametrize("name", ["critique", "compound", "use"])
def test_validator_passes_real_advisory_with_nongate_call(tmp_path, name):
    # real skills that call NON-gate harness scripts (emit_observation / findings_store
    # / disabled_skills) must not trip the closed-enum tripwire (F-D)
    d = _copy_real_with_injectable(tmp_path, name, True)
    res = css.check_skill(str(d))
    assert "injectable-gate-conflict" not in [f["rule"] for f in res["findings"]]


def test_validator_passes_clean_advisory(tmp_path):
    d = _write_skill(tmp_path / "clean",
                     "name: hs:clean\ninjectable: true\n",
                     "# Clean\n\nJust advisory prose, no calls.\n")
    assert "injectable-gate-conflict" not in [
        f["rule"] for f in css.check_skill(str(d))["findings"]]


def test_validator_false_with_gate_call_ok(tmp_path):
    d = _write_skill(tmp_path / "exec",
                     "name: hs:exec\ninjectable: false\n",
                     "# Exec\n\n```bash\npython3 harness/hooks/gate_stage.py\n```\n")
    assert "injectable-gate-conflict" not in [
        f["rule"] for f in css.check_skill(str(d))["findings"]]


def test_validator_greps_body_not_frontmatter(tmp_path):
    # a gate basename sitting in the FRONTMATTER must not trip (body-only, F8)
    d = _write_skill(tmp_path / "fm",
                     "name: hs:fm\ninjectable: true\nnote: python3 harness/hooks/gate_stage.py\n",
                     "# FM\n\nClean body.\n")
    assert "injectable-gate-conflict" not in [
        f["rule"] for f in css.check_skill(str(d))["findings"]]


def test_write_gate_blocks_injectable_conflict(tmp_path):
    d = _write_skill(tmp_path / "faux",
                     "name: hs:faux\ninjectable: true\n",
                     "# Faux\n\n```bash\npython3 harness/scripts/plan_approval.py\n```\n")
    reason = css.write_gate_reason(str(d / "SKILL.md"))
    assert reason and "injectable-gate-conflict" in reason


# --- (c) is_injectable fail-closed ------------------------------------------
def test_absent_injectable_is_not_injectable(tmp_path):
    d = _write_skill(tmp_path / "noflag", "name: hs:noflag\n", "# No flag\n")
    assert css.is_injectable(str(d)) is False


def test_is_injectable_true_false(tmp_path):
    dt = _write_skill(tmp_path / "y", "name: hs:y\ninjectable: true\n", "# y\n")
    df = _write_skill(tmp_path / "n", "name: hs:n\ninjectable: false\n", "# n\n")
    assert css.is_injectable(str(dt)) is True
    assert css.is_injectable(str(df)) is False


# --- (a) bootstrap classifier -----------------------------------------------
@pytest.mark.parametrize("name", ["plan", "cook", "test", "ship", "git",
                                  "code-review", "gemini", "remember"])
def test_bootstrap_proposes_spine_false(name):
    assert ib.classify(name) is False


@pytest.mark.parametrize("name", ["research", "review-pr", "critique", "scout",
                                  "understand", "docs", "port", "techstack"])
def test_bootstrap_proposes_advisory_true(name):
    # review-pr is spine_false; the rest are advisory-true
    expected = False if name == "review-pr" else True
    assert ib.classify(name) is expected


def test_bootstrap_covers_disabled_skills(tmp_path):
    # the repo's disabled-skills/ is empty (off via the dev farm), so synthesize a
    # stash: a skills dir + a sibling disabled-skills dir each with one SKILL.md
    root = tmp_path / "plugins" / "hs"
    live = root / "skills" / "research"
    _write_skill(live, "name: hs:research\n", "# research\n")
    stashed = root / "disabled-skills" / "shopify"
    _write_skill(stashed, "name: hs:shopify\n", "# shopify\n")
    table = ib.propose(skills_root=str(root / "skills"))
    assert "research" in table and "shopify" in table


def test_bootstrap_no_write_without_confirm(tmp_path, capsys):
    d = _write_skill(tmp_path / "skills" / "research", "name: hs:research\n", "# r\n")
    ib.main(["--propose", "--skills-root", str(tmp_path / "skills")])
    # --propose prints a table but writes nothing
    assert "injectable:" not in (d / "SKILL.md").read_text(encoding="utf-8")


def test_bootstrap_apply_idempotent(tmp_path):
    d = _write_skill(tmp_path / "skills" / "cook", "name: hs:cook\n", "# c\n")
    ib.apply_field(str(d / "SKILL.md"), False)
    ib.apply_field(str(d / "SKILL.md"), False)
    text = (d / "SKILL.md").read_text(encoding="utf-8")
    assert text.count("injectable:") == 1
    assert "injectable: false" in text


def test_apply_field_returns_true_only_on_change(tmp_path):
    # honest return: first write changes the file, an idempotent re-apply does not
    d = _write_skill(tmp_path / "skills" / "cook", "name: hs:cook\n", "# c\n")
    md = str(d / "SKILL.md")
    assert ib.apply_field(md, False) is True     # inserted
    assert ib.apply_field(md, False) is False    # no-op re-apply
    assert ib.apply_field(md, True) is True       # value flipped


def test_every_owned_skill_has_injectable():
    # coverage: after migrate, every live hs:* skill carries the field
    missing = []
    for d in sorted(_REAL_SKILLS.iterdir()):
        if not (d / "SKILL.md").is_file() or d.name in {"_shared", "common", "_docslib"}:
            continue
        fm = css._frontmatter(d / "SKILL.md") or {}
        if "injectable" not in fm:
            missing.append(d.name)
    assert missing == [], "skills missing injectable: %s" % missing
