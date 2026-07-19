"""Prose contracts for the workflow-surface consumers (dev-repo only).

P4 wires the multi-lens fan-out consumers (critique, predict, scout,
security-scan) to the shared `hs:base-fanout-consolidate` workflow while keeping
the mandatory non-Workflow fallback. These guards pin the shipped guidance:

- the consumer names the base under its `hs:` plugin namespace (the bare name
  does not resolve);
- it documents the mandatory inline fallback (Workflows are plan-gated);
- it stamps which path ran, from the shared three-label vocabulary;
- it routes the reader to the orchestration-protocol rule.

The runtime behaviour of the base workflow itself is proven by its smoke runs
(P1) and the parity measurement (P3); these are documentation-drift guards, not
behaviour tests.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SKILLS = _ROOT / "harness" / "plugins" / "hs" / "skills"

# Fan-out consumers share base-fanout-consolidate; code-review uses the pipeline base.
FANOUT_CONSUMERS = ["critique", "predict", "scout", "security-scan"]

STAMP_LABELS = ("Workflow(name)", "Workflow(scriptPath)", "inline-Task fallback")


def _skill_body(name):
    path = _SKILLS / name / "SKILL.md"
    assert path.is_file(), f"SKILL.md missing for {name}: {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.dev_repo
@pytest.mark.parametrize("skill", FANOUT_CONSUMERS)
def test_consumer_names_base_fanout_under_namespace(skill):
    body = _skill_body(skill)
    assert "hs:base-fanout-consolidate" in body, (
        f"{skill} must call the base under its hs: namespace (bare name does not resolve)"
    )


@pytest.mark.dev_repo
@pytest.mark.parametrize("skill", FANOUT_CONSUMERS)
def test_consumer_documents_fallback_and_stamp(skill):
    body = _skill_body(skill)
    low = body.lower()
    assert "fallback" in low, f"{skill} must document the mandatory non-Workflow fallback"
    for stamp in STAMP_LABELS:
        assert stamp in body, f"{skill} missing stamp label {stamp!r}"
    assert "orchestration-protocol" in body, (
        f"{skill} must route to harness/rules/orchestration-protocol.md for opt-in resolution"
    )


@pytest.mark.dev_repo
def test_code_review_uses_pipeline_base():
    # code-review (P2) uses the pipeline base, not the fan-out base.
    body = _skill_body("code-review")
    assert "hs:base-pipeline-verify" in body


# --- D7: route anchored at the numbered execution step, not an orphan section --

import re

_ROOT_DIR = Path(__file__).resolve().parents[2]
_RECALL_MODE = _SKILLS / "code-review" / "references" / "recall-mode.md"
_SKILL_DEPS = _ROOT_DIR / "harness" / "data" / "skill-deps.yaml"

ROUTE_TOKEN = "hs:workflow-orchestrate"

# {skill: execution-section anchor}. Every anchor points at the NUMBERED execution
# section (where the fan-out actually happens) — never "Orchestration" (that trailing
# standalone section is the orphan this invariant kills). team keys on its four
# direct-invoke template headings and is checked by its own all-templates test.
ENFORCE_ANCHORS = {
    "code-review": "Workflow",        # step 4 Stage-2 region (+ recall-mode ladder grep)
    "critique": "Process",            # step 2 "Fan out the lenses"
    "predict": "Debate process",      # step 3
    "scout": "Workflow",              # step 4 spawn region
    "security-scan": "Workflow",      # step-4 persona fan-out block
    "research": "Delegate",           # numbered step 6 "Delegate when needed"
    "team": "ON `/hs:team",           # 4 direct-invoke templates (all checked)
}
_NON_TEAM = [s for s in ENFORCE_ANCHORS if s != "team"]
# The 6 skills that ship a trailing `## Orchestration` orphan to strip (team has none).
_ORPHAN_SKILLS = _NON_TEAM


def _section_span(body, anchor):
    """Return the text from the FIRST heading- or numbered-bold-step line that
    contains `anchor`, up to the next top-level `## ` heading (first-match, M5)."""
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        s = line.strip()
        is_heading = s.startswith("#")
        is_step = bool(re.match(r"^\d+\.\s+\*\*", s))
        if (is_heading or is_step) and anchor in s:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].lstrip().startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


def _strip_orchestration_section(body):
    """Remove any `## ...Orchestration...` section (heading → next `## ` or EOF)."""
    lines = body.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("## ") and "Orchestration" in lines[i]:
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("## "):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


@pytest.mark.dev_repo
@pytest.mark.parametrize("skill", _NON_TEAM)
def test_route_anchored_in_execution_section(skill):
    body = _skill_body(skill)
    span = _section_span(body, ENFORCE_ANCHORS[skill])
    assert span is not None, f"{skill}: no section matches anchor {ENFORCE_ANCHORS[skill]!r}"
    assert ROUTE_TOKEN in span, (
        f"{skill}: '{ROUTE_TOKEN}' must appear INSIDE the execution section "
        f"(anchor {ENFORCE_ANCHORS[skill]!r}), not only in a trailing '## Orchestration'"
    )


@pytest.mark.dev_repo
@pytest.mark.parametrize("skill", _ORPHAN_SKILLS)
def test_route_not_only_in_orphan_orchestration(skill):
    body = _skill_body(skill)
    stripped = _strip_orchestration_section(body)
    assert ROUTE_TOKEN in stripped, (
        f"{skill}: route disappears when the '## Orchestration' orphan is stripped — "
        "it must live at the numbered execution step"
    )


@pytest.mark.dev_repo
def test_team_route_in_all_direct_invoke_templates():
    body = _skill_body("team")
    lines = body.splitlines()
    # locate every `## ON /hs:team ...` template heading
    heads = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("## ON `/hs:team")]
    assert len(heads) >= 4, f"expected 4 direct-invoke templates, found {len(heads)}"
    for idx, start in enumerate(heads):
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].lstrip().startswith("## "):
                end = j
                break
        section = "\n".join(lines[start:end])
        assert ROUTE_TOKEN in section, (
            f"team template #{idx + 1} ({lines[start].strip()!r}) is missing the "
            f"'{ROUTE_TOKEN}' assess/route step before its SPAWN"
        )


@pytest.mark.dev_repo
def test_span_finder_first_match_on_shared_substring():
    body = "\n".join([
        "## Workflow position",
        "route: NOT here",
        "## Workflow",
        f"route: {ROUTE_TOKEN} here",
        "## Next",
    ])
    # first-match must pick `## Workflow position` (the earlier shared-substring head)
    span = _section_span(body, "Workflow")
    assert "NOT here" in span
    assert ROUTE_TOKEN not in span


@pytest.mark.dev_repo
def test_recall_mode_ladder_mentions_route():
    assert _RECALL_MODE.is_file(), f"recall-mode.md missing: {_RECALL_MODE}"
    body = _RECALL_MODE.read_text(encoding="utf-8")
    assert ROUTE_TOKEN in body, (
        "recall-mode.md four-tier ladder must mention the route (whole-file grep)"
    )


@pytest.mark.dev_repo
def test_team_deps_include_workflow_orchestrate():
    assert _SKILL_DEPS.is_file(), f"skill-deps.yaml missing: {_SKILL_DEPS}"
    import yaml
    data = yaml.safe_load(_SKILL_DEPS.read_text(encoding="utf-8"))
    team_deps = data["skills"]["team"]["deps"]
    assert "workflow-orchestrate" in team_deps, (
        "team now routes at a numbered step -> it must declare the workflow-orchestrate dep"
    )


@pytest.mark.dev_repo
def test_setup_guides_workflow_subagent_deny_list_scope():
    # hs:setup must teach the deny-list write-scope: a Workflow `--fix` (via a
    # workflow-subagent) edits project source out of the box — no lane/overlay setup.
    body = _skill_body("setup")
    assert "deny-list" in body.lower()
    assert "workflow-subagent" in body
    assert "out of the box" in body.lower()
