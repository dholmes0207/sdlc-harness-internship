"""partner-relayer agent contract (phase 6, twin of test_gemini_agents.py).

ONE passthrough agent wraps partner_companion.py: it runs it OUT of the main
thread (context isolation), pins haiku, carries only Bash+Read, and makes
EXACTLY ONE companion call per spawn — the body must reference the real
script it shells out to. maxTurns must be in [3,4]: the loop stops the moment
the turn count reaches the cap, so the envelope-emit turn only fires while the
budget is unspent. A courier is turn 1 = the companion tool_use + a turn to
surface stdout, so the probe-verified floor is 2 (maxTurns:1 terminates right
after the tool_use — error_max_turns, result=None — returning an EMPTY
envelope). Each stray tool-use turn (a defensive Read, a retried Bash) before
the intended call pushes the floor up by one; 3 buys one stray of margin. The
upper bound keeps the cap deliberately TIGHT (it bounds a runaway courier's
real companion spawns). The one-call-only contract is enforced by the agent's
prose, not by starving its turn budget.
"""
import re
from pathlib import Path

_AGENT = (Path(__file__).resolve().parent.parent / "plugins" / "hs" / "agents"
          / "partner-relayer.md")


def _text():
    return _AGENT.read_text(encoding="utf-8")


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[:end] if end != -1 else text


def _body(text):
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    after = text.find("\n", end + 1) if end != -1 else -1
    return text[after + 1:] if after != -1 else ""


def test_relayer_exists():
    assert _AGENT.is_file()


def test_relayer_runs_partner_companion():
    text = _text()
    body = _body(text)
    assert "partner_companion.py" in body

    fm = _frontmatter(text)
    m = re.search(r"^maxTurns:\s*(\d+)\s*$", fm, re.MULTILINE)
    # [3,4], not >=2: 2 is the bare floor with zero slack — a single stray tool-use
    # turn (defensive Read / retried Bash) before the companion call exhausts the
    # budget and the courier returns an EMPTY envelope. 3 buys one stray of margin;
    # the upper bound keeps the cap tight (bounds a runaway courier's real spawns).
    assert m and 3 <= int(m.group(1)) <= 4

    m = re.search(r"^model:\s*(\S+)\s*$", fm, re.MULTILINE)
    assert m and m.group(1).strip().lower() == "haiku"


def test_relayer_never_chooses_provider():
    body = _body(_text()).lower()
    # the relayer is a dumb courier — Claude picks the provider, not the agent
    assert "you never" in body or "never choose" in body or "does not choose" in body
