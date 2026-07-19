"""Every plugin agent frontmatter pins an explicit model.

CC's default subagent model can shift under the session; a blank `model:` silently inherits
Opus (or whatever the future default is). Pinning every agent keeps the cost profile
explicit. This asserts 0 agents in plugins/hs/agents/ leave model unset, with a value in the
allowed tier set.
"""
import re
from pathlib import Path

_AGENTS = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "agents"
# `fable` is a valid explicit pin (the strongest tier); it is UNRANKED in the model-policy
# tier ladder (exact-only, never a ceiling/floor endpoint) but is still a real, non-blank
# model an agent may pin — an advisory counsel agent does.
_ALLOWED = {"opus", "sonnet", "haiku", "fable"}
_MODEL_LINE = re.compile(r"^model:\s*(\S+)\s*$", re.MULTILINE)


def _frontmatter(text: str) -> str:
    # Frontmatter is the block between the first two '---' fences.
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[:end] if end != -1 else text


def test_every_agent_pins_a_valid_model():
    files = sorted(_AGENTS.glob("*.md"))
    assert files, "no agent files found under %s" % _AGENTS
    bad = []
    for f in files:
        fm = _frontmatter(f.read_text(encoding="utf-8"))
        m = _MODEL_LINE.search(fm)
        if not m:
            bad.append((f.name, "no model:"))
        elif m.group(1).strip().lower() not in _ALLOWED:
            bad.append((f.name, "model=%s not in %s" % (m.group(1), sorted(_ALLOWED))))
    assert not bad, "agents missing/invalid model pin: %s" % bad
