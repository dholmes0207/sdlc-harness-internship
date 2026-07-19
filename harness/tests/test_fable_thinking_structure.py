"""hs:fable-thinking skill structure: thin-core frontmatter, the three references,
a passing/non-confusable description, verbatim-ported protocol prose (line count +
headings preserved), and no upstream dev-id / runtime-slot leaks.

The reasoning-protocol BODY is a verbatim port (D15): only mechanical token
renames (ak: -> hs:, upstream dev-id stripped) and the frontmatter (adapted to
the harness description contract) may differ from upstream.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _ROOT / "plugins" / "hs" / "skills" / "fable-thinking"
_SKILL_MD = _SKILL / "SKILL.md"
_REFS = ("worked-examples.md", "design-taste.md", "content-taste.md")

_DOT_CLAUDE = "." + "claude/"
_FORBIDDEN = (_DOT_CLAUDE + "skills/", _DOT_CLAUDE + "hooks/",
              "kong" + "ming", "Claude" + "Kit", "agent" + "kit")
# A real skill route `ak:name` — the negative-lookbehind skips words like
# "break:" whose "ak:" is preceded by a letter.
_AK_ROUTE = re.compile(r"(?<![a-z])ak:[a-z]")

_HEADINGS = (
    "## The Floor",
    "## Proportionality Gate",
    "## The Constraint Loop",
    "## The Five Moves",
    "### Move 1 — FRAME",
    "### Move 5 — DELIVER",
    "## Claim Discipline",
    "## Self-Review Gate",
    "## Anti-Patterns",
)


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
    fm, text = _frontmatter(_SKILL_MD)
    assert fm.get("name") == "hs:fable-thinking"
    assert fm.get("description"), "no description"
    assert fm.get("injectable") in ("true", "false"), "injectable bool required"
    assert "compliance-tier" in text, "compliance-tier required in metadata"


def test_three_references_exist():
    for r in _REFS:
        assert (_SKILL / "references" / r).is_file(), "missing reference %s" % r


def test_prose_fidelity_line_count_and_headings():
    text = _SKILL_MD.read_text(encoding="utf-8")
    n = len(text.splitlines())
    assert 380 <= n <= 415, "SKILL.md line count %d drifted from the ~400-line port" % n
    for h in _HEADINGS:
        assert h in text, "ported protocol heading missing: %r" % h


def test_references_carry_the_worked_traces():
    # worked-examples must keep its four end-to-end traces (verbatim body).
    we = (_SKILL / "references" / "worked-examples.md").read_text(encoding="utf-8")
    assert "Move 3 — REASON" in we, "worked-examples lost its move-labelled trace"


def test_no_forbidden_literals_or_ak_routes():
    files = [_SKILL_MD] + [_SKILL / "references" / r for r in _REFS]
    for p in files:
        text = p.read_text(encoding="utf-8")
        for lit in _FORBIDDEN:
            assert lit not in text, "forbidden token %r in %s" % (lit, p.name)
        assert not _AK_ROUTE.search(text), "leaked ak: route in %s" % p.name


def test_description_passes_score_and_is_not_confusable():
    out = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "score_skill_description.py"),
         str(_ROOT / "plugins"), "--json"],
        capture_output=True, text=True, timeout=120)
    data = json.loads(out.stdout)
    fable = next((s for s in data["scores"]
                  if s["skill_name"] == "hs:fable-thinking"), None)
    assert fable is not None, "hs:fable-thinking not scored"
    assert fable["passed"], "description below threshold: %s" % fable.get("issues")
