"""Tests for the hs:workflow-orchestrate lead scripts.

Two structural-only scripts back the skill's directives:
- plan_orchestration.py  -> deterministic strategy proposal (mode/groups/template/report-dir)
- write_finding.py       -> early-write one finding to the run's refs dir

Loaded by file path so the tests do not depend on the skill dir being importable
as a package.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills" / "workflow-orchestrate"
PLAN = SKILL / "scripts" / "plan_orchestration.py"
WRITE = SKILL / "scripts" / "write_finding.py"


def _run(script: Path, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"{script.name} exited {proc.returncode}: {proc.stderr}"
    return json.loads(proc.stdout)


# ---- plan_orchestration.py -------------------------------------------------

def test_plan_multistage_barrier_picks_workflow():
    out = _run(PLAN, "--run-id", "r1", "--groups", "research:4,critique:6,recommend:1",
               "--barrier", "--determinism")
    assert out["mode"] == "workflow"
    assert out["sub_count"] == 11
    assert {g["key"] for g in out["groups"]} == {"research", "critique", "recommend"}
    assert out["report_dir"].rstrip("/").endswith("r1")


def test_plan_small_rittic_picks_subagents_inline_task():
    out = _run(PLAN, "--run-id", "r2", "--groups", "scan:2")
    assert out["mode"] == "subagents"
    assert out["template"] == "inline-task"
    assert out["batch_size"] == 2  # respects the 2-subagent-per-turn limit by default


def test_plan_fanout_two_stage_selects_base_fanout_template():
    out = _run(PLAN, "--run-id", "r3", "--groups", "lens:5", "--fanout", "--barrier")
    assert out["mode"] == "workflow"  # sub_count>=6? no (5) but barrier forces workflow
    assert out["template"] == "hs:base-fanout-consolidate"


def test_plan_find_verify_selects_pipeline_template():
    out = _run(PLAN, "--run-id", "r4", "--groups", "dim:3,verify:3", "--find-verify")
    assert out["template"] == "hs:base-pipeline-verify"


def test_plan_product_flag_routes_report_dir_under_product_refs():
    out = _run(PLAN, "--run-id", "ctx-min", "--groups", "research:4", "--product")
    assert out["report_dir"].rstrip("/") == "docs/product/_refs/ctx-min"


# ---- Mode C (agent teams) + exec-gate policy -------------------------------

def test_plan_team_mode_when_coordinating_and_longlived():
    out = _run(PLAN, "--run-id", "t1", "--groups", "build:4",
               "--coordinate", "--long-lived")
    assert out["mode"] == "team"
    assert out["template"] == "agent-teams"
    assert out["experimental"] is True
    assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" in out["requires_flag"]
    # must point at the CURRENT api, not resurrect the removed TeamCreate call
    assert "Agent(name=" in out["api"]
    if "TeamCreate" in out["api"]:
        assert "removed" in out["api"].lower()


def test_plan_team_not_picked_without_both_signals():
    # coordinate alone, or long-lived alone, stays A/B — team is the narrow case
    a = _run(PLAN, "--run-id", "t2", "--groups", "x:2", "--coordinate")
    assert a["mode"] != "team"
    b = _run(PLAN, "--run-id", "t3", "--groups", "x:2", "--long-lived")
    assert b["mode"] != "team"


def test_plan_mode_override_forces_team():
    out = _run(PLAN, "--run-id", "t4", "--groups", "x:1", "--mode", "team")
    assert out["mode"] == "team"
    assert out["experimental"] is True


def test_exec_gate_workflow_auto_under_ultracode():
    out = _run(PLAN, "--run-id", "g1", "--groups", "lens:6", "--ultracode")
    assert out["mode"] == "workflow"
    assert out["exec"]["gate"] == "auto"


def test_exec_gate_workflow_confirm_required_without_ultracode():
    out = _run(PLAN, "--run-id", "g2", "--groups", "lens:6")
    assert out["mode"] == "workflow"
    assert out["exec"]["gate"] == "confirm_required"
    # the whole point: never silently drop to inline subagents
    assert out["exec"]["no_silent_downgrade"] is True


def test_exec_gate_team_always_confirm_even_with_ultracode():
    out = _run(PLAN, "--run-id", "g3", "--groups", "build:3",
               "--coordinate", "--long-lived", "--ultracode")
    assert out["mode"] == "team"
    assert out["exec"]["gate"] == "confirm_required"


def test_exec_gate_subagents_auto():
    out = _run(PLAN, "--run-id", "g4", "--groups", "scan:2")
    assert out["mode"] == "subagents"
    assert out["exec"]["gate"] == "auto"


# ---- token-budget-aware sizing ---------------------------------------------

def test_budget_absent_emits_no_block():
    out = _run(PLAN, "--run-id", "b0", "--groups", "research:4")
    assert "token_budget" not in out


def test_budget_within_capacity_leaves_no_trim():
    # 4 subs, 1M budget @ 100k/sub -> capacity 10 >= 4 -> fits
    out = _run(PLAN, "--run-id", "b1", "--groups", "research:4", "--token-budget", "1000000")
    assert out["token_budget"]["within_budget"] is True
    assert out["token_budget"]["capacity"] == 10
    assert out["token_budget"]["trim_advice"] is None
    assert out["sub_count"] == 4  # non-destructive: original ask untouched


def test_budget_over_emits_proportional_trim():
    # 11 subs, 500k @ 100k -> capacity 5 -> must trim, floor 1/group
    out = _run(PLAN, "--run-id", "b2", "--groups", "a:4,b:6,c:1", "--token-budget", "500000")
    b = out["token_budget"]
    assert b["within_budget"] is False
    ta = b["trim_advice"]
    assert ta["total_subs"] == 5
    assert sum(g["subs"] for g in ta["groups"]) == 5
    assert all(g["subs"] >= 1 for g in ta["groups"])  # no group starved below 1
    assert ta["dropped_subs"] == 6
    # widest original group (b:6) should keep the most after trim
    by_key = {g["key"]: g["subs"] for g in ta["groups"]}
    assert by_key["b"] >= by_key["a"] >= by_key["c"]


def test_budget_custom_per_sub_cost_changes_capacity():
    out = _run(PLAN, "--run-id", "b3", "--groups", "x:8", "--token-budget", "300000",
               "--per-sub-cost", "50000")
    assert out["token_budget"]["capacity"] == 6  # 300k / 50k


def test_budget_too_small_for_group_count_drops_tail():
    # capacity 1 < 3 groups -> keep first group @1, drop the tail two by name
    out = _run(PLAN, "--run-id", "b4", "--groups", "a:2,b:2,c:2", "--token-budget", "100000")
    ta = out["token_budget"]["trim_advice"]
    assert ta["total_subs"] == 1
    assert ta["dropped_groups"] == ["b", "c"]


def test_budget_zero_capacity_drops_everything():
    out = _run(PLAN, "--run-id", "b5", "--groups", "a:2", "--token-budget", "50000")
    ta = out["token_budget"]["trim_advice"]
    assert ta["total_subs"] == 0
    assert ta["dropped_subs"] == 2


def test_budget_rejects_nonpositive():
    proc = subprocess.run(
        [sys.executable, str(PLAN), "--run-id", "b6", "--groups", "a:1", "--token-budget", "0"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2


# ---- assessment: reason/strategy/scope + advisory scoring (D2/D10/D11) ------

def test_reason_strategy_scope_reflected_verbatim():
    out = _run(PLAN, "--run-id", "a1", "--groups", "x:1",
               "--reason", "hs:critique fixed 4-lens set",
               "--strategy", "subagents base-fanout-consolidate x:1",
               "--scope", "1 file critique/SKILL.md")
    assert out["inputs"]["reason"] == "hs:critique fixed 4-lens set"
    assert out["inputs"]["strategy"] == "subagents base-fanout-consolidate x:1"
    assert out["inputs"]["scope"] == "1 file critique/SKILL.md"


def test_assessment_complexity_counts_knobs():
    out = _run(PLAN, "--run-id", "a2", "--groups", "a:3,b:3,c:3", "--barrier")
    a = out["assessment"]
    # stages>1 + barrier + sub_count>=6 all fire -> complexity clearly high
    assert a["complexity"] >= 3
    assert a["route_depth"] == "agent"  # wide + barrier is never a light bypass


def test_route_depth_light_when_simple_and_confident():
    # content-rich fields (cite-token reason, mode+template strategy, bounded scope)
    # + a single small group -> the cheap heuristic bypass.
    out = _run(PLAN, "--run-id", "a3", "--groups", "a:1",
               "--reason", "hs:scout fixed exploration",
               "--strategy", "subagents base-pipeline-verify a:1",
               "--scope", "1 dir src/auth")
    a = out["assessment"]
    assert a["complexity"] <= 2
    assert a["confidence"] >= 3
    assert a["route_depth"] == "light"
    assert a["flags"] == []


def test_route_depth_agent_when_missing_fields():
    out = _run(PLAN, "--run-id", "a4", "--groups", "a:1")
    a = out["assessment"]
    assert a["route_depth"] == "agent"
    assert "missing-fields" in a["flags"]


def test_confidence_bit_is_content_not_presence():
    # all three fields present but hollow -> must NOT reach light (I2 fix).
    out = _run(PLAN, "--run-id", "a5", "--groups", "a:1",
               "--reason", "just do it", "--strategy", "stuff", "--scope", "all")
    a = out["assessment"]
    assert a["confidence"] < 3
    assert a["route_depth"] == "agent"
    assert "unbounded-scope" in a["flags"]
    assert "no-evidence" in a["flags"]


def test_over_cap_flag():
    groups = ",".join(f"g{i}:1" for i in range(12))
    out = _run(PLAN, "--run-id", "a6", "--groups", groups, "--group-cap", "3")
    a = out["assessment"]
    assert a["cap"] == 3
    assert a["over_cap"] is True
    assert "over-cap" in a["flags"]


def test_no_cap_arg_disables_over_cap():
    groups = ",".join(f"g{i}:1" for i in range(12))
    out = _run(PLAN, "--run-id", "a7", "--groups", groups)
    a = out["assessment"]
    assert a["cap"] is None
    assert a["over_cap"] is False
    assert "over-cap" not in a["flags"]


def test_one_sub_per_finding_red_flag():
    groups = ",".join(f"g{i}:1" for i in range(8))  # 8 groups == 8 subs, all 1
    out = _run(PLAN, "--run-id", "a8", "--groups", groups)
    assert "one-sub-per-finding" in out["assessment"]["flags"]


def test_script_emits_no_hardgate():
    # D2: the script reflects + scores, it never blocks. No block/refuse verdict key.
    out = _run(PLAN, "--run-id", "a9", "--groups", "a:3,b:3", "--barrier")
    banned = {"blocked", "refuse", "refused", "denied"}

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k.lower() not in banned, f"hard-gate key leaked: {k}"
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(out)


# ---- write_finding.py ------------------------------------------------------

def test_write_finding_creates_and_appends(tmp_path):
    base = str(tmp_path / "refs")
    o1 = _run(WRITE, "--run-id", "run1", "--group", "research", "--title", "First",
              "--body", "alpha", "--refs-base", base, "--ts", "2026-07-03T00:00:00Z")
    p = Path(o1["path"])
    assert p.exists() and p.name == "research.md"
    assert o1["appended"] is False  # first write

    o2 = _run(WRITE, "--run-id", "run1", "--group", "research", "--title", "Second",
              "--body", "beta", "--refs-base", base, "--ts", "2026-07-03T00:01:00Z")
    assert o2["appended"] is True
    text = p.read_text()
    assert "First" in text and "Second" in text
    assert "alpha" in text and "beta" in text


def test_write_finding_product_routes_under_product_refs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    o = _run(WRITE, "--run-id", "ctx-min", "--group", "R1", "--title", "T",
             "--body", "x", "--product")
    assert Path(o["path"]) == Path("docs/product/_refs/ctx-min/R1.md")
    assert Path(o["path"]).exists()


def test_scripts_have_hook_class_free_stdout_is_pure_json():
    # structural scripts emit ONLY json on stdout (no telemetry noise)
    out = _run(PLAN, "--run-id", "r", "--groups", "a:1")
    assert isinstance(out, dict) and "mode" in out
