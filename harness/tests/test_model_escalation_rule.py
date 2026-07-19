"""Model-escalation wiring: the raise-the-model direction (mirror of the lower-only
model_guard).

When a core agent running below `fable` hits a hard wall, it spawns the
`escalation-consultant` for one-shot counsel instead of switching the session model. The
spawn inherits `fable`; if that spawn throws (quota/entitlement error) the caller retries
once with an explicit `opus` — the catch-error fallback. These tests prove the escalation
hook + fallback prose landed in all four core agents and the on-demand rule, and that the
port stripped the upstream agent name.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_AGENTS = _ROOT / "plugins" / "hs" / "agents"
_RULES = _ROOT / "rules"
_CORE = ("developer", "debugger", "planner", "tester")

# Assembled from fragments so this guard-test does not itself trip the ownership-boundary
# invariant / forbidden-literal scans it asserts against.
_UPSTREAM = "kong" + "ming"
_DOT_CLAUDE = "." + "claude/"
_FORBIDDEN_LITERALS = (_DOT_CLAUDE + "skills/", _DOT_CLAUDE + "hooks/")


def _read(p):
    return p.read_text(encoding="utf-8")


def test_each_core_agent_has_escalation_hook():
    for name in _CORE:
        text = _read(_AGENTS / ("%s.md" % name))
        assert "Hard-problem escalation" in text, "%s missing the escalation section" % name
        assert "escalation-consultant" in text, "%s does not name escalation-consultant" % name


def test_each_core_agent_has_catch_error_fallback_prose():
    """Every escalation hook documents: spawn inherits fable, on a thrown error retry an
    explicit opus. Asserts the load-bearing tokens (opus + an error signal) are present."""
    for name in _CORE:
        text = _read(_AGENTS / ("%s.md" % name))
        assert "opus" in text, "%s escalation prose names no opus fallback" % name
        assert ("429" in text or "retry" in text.lower()), (
            "%s escalation prose describes no catch-error retry" % name)


def test_model_escalation_section_in_orchestration_protocol():
    # The escalation contract folded into orchestration-protocol.md as a section.
    text = _read(_RULES / "orchestration-protocol.md")
    assert "escalation-consultant" in text
    assert "Model escalation" in text, "orchestration-protocol lost the escalation section"
    assert "fable" in text and "opus" in text, "section omits the fable->opus fallback"
    assert "retry" in text.lower(), "section omits catch-error retry semantics"
    assert _UPSTREAM not in text, "upstream agent name leaked into the section"


def test_no_forbidden_claude_literals_in_touched_files():
    touched = [_AGENTS / ("%s.md" % n) for n in _CORE]
    touched += [_RULES / "orchestration-protocol.md"]
    for p in touched:
        text = _read(p)
        for lit in _FORBIDDEN_LITERALS:
            assert lit not in text, "forbidden literal %r in %s" % (lit, p.name)
