"""hs:issue-to-plan structure: an audit-gated issue→plan pipeline that STOPS at a validated
plan (never cooks/ships), with the five-outcome gate + comment/label + stop-rule ported intact.
"""
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _ROOT / "plugins" / "hs" / "skills" / "issue-to-plan"

_DOT_CLAUDE = "." + "claude/"
_FORBIDDEN = (_DOT_CLAUDE + "skills/", _DOT_CLAUDE + "hooks/", "kong" + "ming", "Claude" + "Kit", "ak:", "ak-")


def _frontmatter(md_path):
    text = md_path.read_text(encoding="utf-8")
    body = text.split("---", 2)
    fm = {}
    for line in body[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, text


def test_skill_frontmatter():
    fm, text = _frontmatter(_SKILL / "SKILL.md")
    assert fm.get("name") == "hs:issue-to-plan"
    assert fm.get("description"), "no description"
    assert "compliance-tier" in text, "compliance-tier required"


def test_audit_gate_has_five_outcomes_and_templates():
    text = (_SKILL / "references" / "audit-gate.md").read_text(encoding="utf-8").lower()
    for outcome in ("proceed", "needs decision", "duplicate", "reject", "not worth"):
        assert outcome in text, "audit-gate missing outcome %r" % outcome
    assert "label" in text and "comment" in text, "audit-gate missing comment/label template"


def test_stop_rule_before_implement():
    text = (_SKILL / "SKILL.md").read_text(encoding="utf-8").lower()
    # planning-only: it stops at a plan and does not cook/ship/PR
    assert "planning-only" in text or "stops at" in text or "stop before implement" in text, (
        "SKILL.md must state it is planning-only")
    # the stop-rule must forbid planning on a rejected/duplicate outcome
    audit = (_SKILL / "references" / "audit-gate.md").read_text(encoding="utf-8").lower()
    assert "stop" in audit, "audit-gate must carry a stop-rule"


def test_description_passes_score_and_not_confusable_with_vibe_or_plan():
    out = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "score_skill_description.py"),
         str(_ROOT / "plugins"), "--json"],
        capture_output=True, text=True, timeout=120)
    data = json.loads(out.stdout)
    s = next((x for x in data["scores"] if x["skill_name"] == "hs:issue-to-plan"), None)
    assert s is not None, "hs:issue-to-plan not scored"
    assert s["passed"], "description below threshold: %s" % s.get("issues")
    for pair in data.get("confusable_pairs", []):
        names = set(pair.get("pair", pair) if isinstance(pair, dict) else pair)
        assert not ("hs:issue-to-plan" in names and ("hs:vibe" in names or "hs:plan" in names)), (
            "hs:issue-to-plan is confusable with vibe/plan: %s" % (pair,))


def test_no_forbidden_literals_or_ak_names():
    files = [_SKILL / "SKILL.md", _SKILL / "references" / "audit-gate.md"]
    for p in files:
        text = p.read_text(encoding="utf-8")
        for lit in _FORBIDDEN:
            assert lit not in text, "forbidden token %r in %s" % (lit, p.name)
