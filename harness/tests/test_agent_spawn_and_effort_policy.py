"""Agent frontmatter capability policy — spawn lanes, effort tiers, explicit writes.

Ratified in-session against the official CC subagent frontmatter contract
(code.claude.com/docs/en/subagents), after an empirical probe proved the scoped
`Task(Explore)` form is a NO-OP inside a subagent definition: CC ignores the
parenthesised type list, so a `Task(Explore)` agent can in fact spawn any subagent
type (verified: a read-only lens spawned a general-purpose child that wrote a file).

Consequences encoded here:
  * `Task(Explore)` is retired everywhere — the scoped form promised containment it
    never delivered; the real write enforcement is agent_rbac_guard, not the tool syntax.
  * Spawn (a bare `Task`/`Agent`) is granted to every agent EXCEPT four carve-outs:
      - gemini-relayer / workflow-orchestrator — contract says they never spawn;
      - red-teamer / independent-revalidator — epistemic independence: they must
        re-derive from PRIMARY evidence first-hand, never delegating the reading.
  * `effort` pins per-agent reasoning depth (overrides session, replace-semantics,
    model-capability clamped) — adversarial/gate-bearing high, mechanical low.
  * Report-writing lenses declare Write/Edit explicitly instead of smuggling them in
    through the `memory:` auto-grant.
"""
import re
import sys
from pathlib import Path

import yaml

_AGENTS = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "agents"
_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import model_policy  # noqa: E402

# --- ratified decision tables -------------------------------------------------

# The effort SSOT lives in model-policy.yaml (model_policy.effort_for); this table is
# retired — see the effort-tier tests below, which read the policy directly instead of
# hand-maintaining a duplicate. haiku has no effort dial (documented) — the 6 agents below
# must carry no `effort:` in frontmatter and no `effort:` in policy.
_HAIKU_AGENTS = {
    "gemini-relayer", "git-manager", "journal-writer",
    "partner-relayer", "project-manager", "spec-craft-critic",
}

# agents that must NOT carry a subagent-spawn tool
NO_SPAWN = {
    "gemini-relayer", "partner-relayer", "workflow-orchestrator", "red-teamer",
    "independent-revalidator",
    # spec-tech-critic / spec-craft-critic: read-only hs:critique lenses mapped
    # from product-spec's tech-critic/craft-critic (harness/plugins/hs/skills/
    # spec/references/spec-critique.md) — same epistemic-independence rationale
    # as red-teamer/independent-revalidator above: a critique lens must read the
    # scan bundle first-hand, never delegate the reading to a spawned child.
    "spec-tech-critic", "spec-craft-critic",
}

# report-writing lenses that must declare Write + Edit explicitly (not via memory)
EXPLICIT_WRITERS = {
    "code-reviewer", "researcher", "red-teamer", "market-fit-critic",
    "product-value-critic", "independent-revalidator", "critique-consolidator",
}


def _fm(name):
    text = (_AGENTS / (name + ".md")).read_text(encoding="utf-8")
    m = _FM.match(text)
    assert m, "%s.md has no frontmatter" % name
    return yaml.safe_load(m.group(1))


def _tool_tokens(name):
    return {t.strip() for t in str(_fm(name).get("tools", "")).split(",") if t.strip()}


def _skills(name):
    return set(_fm(name).get("skills", []) or [])


def _all_agents():
    return sorted(p.stem for p in _AGENTS.glob("*.md"))


# --- spawn policy -------------------------------------------------------------

def test_scoped_task_explore_retired_everywhere():
    offenders = [n for n in _all_agents() if any("(Explore)" in t for t in _tool_tokens(n))]
    assert not offenders, "Task(Explore) is decorative and must be retired: %s" % offenders


def test_spawn_granted_to_all_but_carve_outs():
    missing = []
    for n in _all_agents():
        if n in NO_SPAWN:
            continue
        if not ({"Task", "Agent"} & _tool_tokens(n)):
            missing.append(n)
    assert not missing, "these agents should carry a bare Task/Agent spawn tool: %s" % missing


def test_carve_outs_have_no_spawn_tool():
    leaked = [n for n in NO_SPAWN if ({"Task", "Agent"} & _tool_tokens(n))]
    assert not leaked, "spawn carve-outs must not gain Task/Agent: %s" % leaked


# --- effort tiers -------------------------------------------------------------

def test_effort_for_reads_policy_as_ssot():
    assert model_policy.effort_for("planner") == "xhigh"
    assert model_policy.effort_for("researcher") == "high"
    assert model_policy.effort_for("tester") == "medium"


def test_effort_for_haiku_agents_is_none():
    for n in _HAIKU_AGENTS:
        assert model_policy.effort_for(n) is None, n


def test_non_haiku_frontmatter_matches_policy_effort():
    bad = []
    for n in _all_agents():
        if n in _HAIKU_AGENTS:
            continue
        got = _fm(n).get("effort")
        want = model_policy.effort_for(n)
        if want is None:
            bad.append((n, "no policy effort for a non-haiku agent"))
        elif got != want:
            bad.append((n, "effort=%r want %r" % (got, want)))
    assert not bad, "effort drift vs policy: %s" % bad


def test_haiku_agents_carry_no_effort():
    bad = []
    for n in _HAIKU_AGENTS:
        fm = _fm(n)
        if "effort" in fm:
            bad.append((n, "frontmatter still declares effort=%r" % fm.get("effort")))
        if model_policy.effort_for(n) is not None:
            bad.append((n, "policy still carries effort for a haiku agent"))
    assert not bad, "haiku agents must carry no effort: %s" % bad


def test_effort_valid_for_model_capability_clamp():
    assert model_policy.effort_valid_for_model("xhigh", "haiku") is False
    assert model_policy.effort_valid_for_model("xhigh", "claude-opus-4-6") is False
    assert model_policy.effort_valid_for_model("xhigh", "claude-opus-4-8") is True
    assert model_policy.effort_valid_for_model("high", "sonnet") is True
    assert model_policy.effort_valid_for_model(None, "haiku") is True


def test_effort_for_fails_open_on_broken_config(tmp_path):
    p = tmp_path / "model-policy.yaml"
    p.write_text("mode: [this: is: not: valid\n  ::::\n", encoding="utf-8")
    env = {"HARNESS_MODEL_POLICY": str(p)}
    assert model_policy.effort_for("planner", env=env) is None

    env2 = {"HARNESS_MODEL_POLICY": str(tmp_path / "nope.yaml")}
    assert model_policy.effort_for("planner", env=env2) is None


# --- explicit writes ----------------------------------------------------------

def test_report_writers_declare_write_explicitly():
    bad = []
    for n in EXPLICIT_WRITERS:
        toks = _tool_tokens(n)
        if "Write" not in toks or "Edit" not in toks:
            bad.append((n, sorted(toks)))
    assert not bad, "report-writing lenses must declare Write+Edit in tools: %s" % bad


# --- per-agent specifics ------------------------------------------------------

def test_brainstormer_can_write_and_learn():
    fm = _fm("brainstormer")
    assert fm.get("memory") == "project", "brainstormer needs memory: project"
    toks = _tool_tokens("brainstormer")
    assert {"Write", "Edit"} <= toks, "brainstormer must declare Write+Edit: %s" % sorted(toks)
    assert {"Task", "Agent"} & toks, "brainstormer must be able to spawn"


def test_project_manager_has_skill_tool():
    assert "Skill" in _tool_tokens("project-manager"), \
        "project-manager activates hs:project-management at runtime — needs Skill tool"


def test_developer_does_not_hardwire_isolation_and_declares_spine_skills():
    fm = _fm("developer")
    assert fm.get("isolation") is None, (
        "developer must NOT hardwire isolation — it is opt-in per spawn: a sequential "
        "phase runs in-place (writes land in the current tree), a parallel slice passes "
        "isolation: worktree explicitly on the spawn"
    )
    assert {"cook", "test"} <= _skills("developer"), "developer preloads cook+test"


def test_tester_and_docs_manager_preload_spine_skills():
    assert "test" in _skills("tester"), "tester preloads the test skill"
    assert {"docs", "repomix"} <= _skills("docs-manager"), "docs-manager preloads docs+repomix"
