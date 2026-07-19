"""hs:advise skill + advisor agent structure: thin-core frontmatter, the four references,
a passing/non-confusable description, the relay-protocol's F5 guardrails, and clean prose.
"""
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _ROOT / "plugins" / "hs" / "skills" / "advise"
_ADVISOR = _ROOT / "plugins" / "hs" / "agents" / "advisor.md"
_REFS = ("interview-protocol.md", "verdict-structure.md", "relay-protocol.md")

_DOT_CLAUDE = "." + "claude/"
_FORBIDDEN = (_DOT_CLAUDE + "skills/", _DOT_CLAUDE + "hooks/", "kong" + "ming", "Claude" + "Kit")


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
    assert fm.get("name") == "hs:advise"
    assert fm.get("description"), "no description"
    assert fm.get("injectable") in ("true", "false"), "injectable bool required"
    assert "compliance-tier" in text, "compliance-tier required in metadata"


def test_four_references_exist():
    assert (_SKILL / "references" / "interview-protocol.md").is_file()
    assert (_SKILL / "references" / "verdict-structure.md").is_file()
    assert (_SKILL / "references" / "relay-protocol.md").is_file()


def test_verdict_structure_has_eight_items():
    text = (_SKILL / "references" / "verdict-structure.md").read_text(encoding="utf-8").lower()
    for item in ("verdict", "should do", "should not", "better", "my take",
                 "benefits", "trade-offs", "checklist"):
        assert item in text, "verdict structure missing %r" % item


def test_advisor_agent_frontmatter():
    fm, _ = _frontmatter(_ADVISOR)
    assert fm.get("name") == "advisor"
    assert fm.get("model"), "advisor needs a model pin"
    assert fm.get("tools"), "advisor needs tools"


def test_relay_protocol_documents_f5_guardrails():
    text = (_SKILL / "references" / "relay-protocol.md").read_text(encoding="utf-8").lower()
    assert "main" in text and "only" in text, "relay-protocol must state the main-only constraint"
    # turn-budget >= 2: never spawn the courier capped at a single turn
    assert "maxturns" in text or "two turn" in text or ">= 2" in text or "at least two" in text, (
        "relay-protocol must pin the >=2 turn budget (no maxTurns:1 empty envelope)")


def test_description_passes_score_and_is_not_confusable():
    out = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "score_skill_description.py"),
         str(_ROOT / "plugins"), "--json"],
        capture_output=True, text=True, timeout=120)
    data = json.loads(out.stdout)
    advise = next((s for s in data["scores"] if s["skill_name"] == "hs:advise"), None)
    assert advise is not None, "hs:advise not scored"
    assert advise["passed"], "hs:advise description below threshold: %s" % advise.get("issues")
    for pair in data.get("confusable_pairs", []):
        names = set(pair.get("pair", pair) if isinstance(pair, dict) else pair)
        assert not ("hs:advise" in names and ("hs:ask" in names or "hs:brainstorm" in names)), (
            "hs:advise is confusable with ask/brainstorm: %s" % (pair,))


def test_no_forbidden_literals():
    files = [_SKILL / "SKILL.md", _ADVISOR] + [_SKILL / "references" / r for r in _REFS]
    files += list((_SKILL / "scripts").glob("*.py"))
    for p in files:
        text = p.read_text(encoding="utf-8")
        for lit in _FORBIDDEN:
            assert lit not in text, "forbidden token %r in %s" % (lit, p.name)
