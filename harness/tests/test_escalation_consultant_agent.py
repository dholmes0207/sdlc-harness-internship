"""Contract for the escalation-consultant advisory agent.

A one-shot strongest-model counsel agent: prefers `fable`, but its caller may fall back to
an explicit `opus` spawn without being hard-blocked. That fallback is only possible if the
agent's model-policy posture is `advisory` (a nudge, never exit-2). These tests prove:
  - the agent file ships with a valid hs frontmatter pinned to `fable`;
  - the RBAC lane matches the advisory-agent template (plans + agent-memory only);
  - a bare inherit passes untouched (self_pinned trusts frontmatter);
  - an explicit opus override is ADVISED, not blocked (the fallback path stays open);
  - the drift guard sees required_model == frontmatter model;
  - no ported AK/dev-id names or forbidden `.claude/...` literals leak into the prose.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _sub in ("scripts", "hooks"):
    p = str(_ROOT / _sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import model_policy  # noqa: E402
import model_guard  # noqa: E402

_AGENT = _ROOT / "plugins" / "hs" / "agents" / "escalation-consultant.md"


def _frontmatter(md_path):
    """Return the frontmatter block (between the first two `---` fences) as a dict of the
    simple `key: value` lines — enough for name/model/memory presence checks."""
    text = md_path.read_text(encoding="utf-8")
    assert text.startswith("---"), "agent file missing frontmatter fence"
    body = text.split("---", 2)
    assert len(body) >= 3, "agent file frontmatter not closed"
    fm = {}
    for line in body[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def test_agent_file_exists_with_valid_frontmatter():
    assert _AGENT.is_file(), "escalation-consultant agent file is missing"
    fm = _frontmatter(_AGENT)
    assert fm.get("name") == "escalation-consultant"
    assert fm.get("model") == "fable"
    assert fm.get("memory"), "agent declares no memory field"
    assert fm.get("tools"), "agent declares no tools field"
    assert fm.get("description"), "agent declares no description"


def test_rbac_deny_list_allows_report_blocks_binary():
    # deny-list: escalation-consultant (advisory-only) writes its report under plans/
    # out of the box and is blocked only by the floor — no per-role whitelist lane.
    import sys as _sys
    import tempfile
    import os as _os
    _sys.path.insert(0, str(_ROOT / "scripts"))
    _sys.path.insert(0, str(_ROOT / "hooks"))
    import agent_permissions as ap
    import write_deny_policy as wdp
    root = tempfile.mkdtemp()
    pol = wdp.assemble_policy([])
    assert ap.decide("escalation-consultant", _os.path.join(root, "plans/reports/r.md"), pol, [root]).block_reason is None
    assert ap.decide("escalation-consultant", _os.path.join(root, "harness/hooks/x.py"), pol, [root]).block_reason


def test_model_policy_posture_is_fable_advisory_self_pinned():
    resolved = model_policy.resolve("escalation-consultant")
    assert resolved["required_model"] == "fable"
    assert resolved["self_pinned"] is True
    assert resolved["mode"] == "advisory"


def test_fable_path_bare_inherit_passes():
    resolved = model_policy.resolve("escalation-consultant")
    verdict = model_policy.evaluate("", "", resolved)
    assert verdict["ok"] is True, "self_pinned bare inherit must pass untouched"


def test_fallback_opus_is_advised_not_blocked():
    """The caller's fable→opus fallback: an explicit opus spawn VIOLATES required_model=fable,
    but `mode: advisory` means the guard nudges and CONTINUES (returns None), never exit-2."""
    resolved = model_policy.resolve("escalation-consultant")
    # evaluate agrees the explicit opus is a violation of the fable pin...
    verdict = model_policy.evaluate("opus", "", resolved)
    assert verdict["ok"] is False, "explicit opus must be a violation of the fable pin"
    # ...yet the guard's decision is advise-and-continue, not block.
    reason = model_guard.core({
        "tool_input": {"subagent_type": "hs:escalation-consultant", "model": "opus"},
    })
    assert reason is None, "advisory posture must continue, not block the opus fallback"


def test_drift_required_model_equals_frontmatter():
    resolved = model_policy.resolve("escalation-consultant")
    fm = _frontmatter(_AGENT)
    assert resolved["required_model"] == fm.get("model") == "fable"


def test_prose_has_no_ported_ak_or_devid_names():
    text = _AGENT.read_text(encoding="utf-8")
    # Tokens assembled from fragments so this guard-test does not itself trip the
    # ownership-boundary invariant (which bans the upstream toolkit's name as a literal).
    forbidden = ("kongming", "Kongming", "Claude" + "Kit", "ak" + "-")
    for name in forbidden:
        assert name not in text, "ported name %r leaked into escalation-consultant.md" % name


def test_prose_has_no_forbidden_claude_literals():
    text = _AGENT.read_text(encoding="utf-8")
    # Needles assembled from fragments so this guard-test does not itself contain the banned
    # `.claude/skills|hooks/` literal that the ownership-boundary invariant forbids in harness/.
    dot_claude = "." + "claude/"
    for forbidden in (dot_claude + "skills/", dot_claude + "hooks/"):
        assert forbidden not in text, "forbidden literal %r in escalation-consultant.md" % forbidden
