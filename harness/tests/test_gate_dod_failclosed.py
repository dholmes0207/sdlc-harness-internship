"""test_gate_dod_failclosed.py — the DoD gate's fail-CLOSED contract.

Subprocess tests pin REAL exit codes (a fail-open dressed as a return value is
exactly what these catch):
  - a genuine evaluator CRASH → fails CLOSED (routed through the test_policy_dod
    guard) + a dod_eval_crash trace: a defect in our OWN evaluator must not
    silently disable the gate. A WRAPPER crash (missing PyYAML / malformed policy)
    fails closed earlier at run_compliance_hook.
  - require_plan:false + no plan → PASS (the single-person carve-out).
  - require_plan:true + no plan → ADVISORY at Layer A (check_stage) — exit 0 +
    [advisory], no block (personal-first).
  - a change-class DERIVATION crash → does NOT block, leaves a dod_derivation_failed
    trace.
  - lowering the test_policy_dod guard below block → a guard_downgraded trace.
  - a real hard DoD FAIL → ADVISORY (exit 0), remote CI enforces.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_GATE = _HOOKS / "gate_stage.py"

_PASS_JUNIT = '<testsuite name="u" tests="3" failures="0" errors="0" skipped="0"/>'
_FAIL_JUNIT = '<testsuite name="u" tests="3" failures="1" errors="0" skipped="0"/>'


def _mk_plan(root: Path, name="260625-0000-dod") -> Path:
    d = root / "plans" / name
    (d / "artifacts" / "results").mkdir(parents=True)
    (d / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    return d


def _verification(plan_dir: Path, checks):
    (plan_dir / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": plan_dir.name, "actor": "user:alice",
        "ts": "2026-06-25T00:00:00+07:00", "verdict": "PASS", "checks": checks,
    }), encoding="utf-8")


def _result(plan_dir: Path, rel: str, body: str):
    (plan_dir / "artifacts" / rel).write_text(body, encoding="utf-8")


def _inject(tmp_path, body):
    """A sitecustomize.py on PYTHONPATH patches a module at interpreter startup;
    gate_stage's later `import` returns the same (patched) cached module."""
    d = tmp_path / "inject"
    d.mkdir(exist_ok=True)
    (d / "sitecustomize.py").write_text(
        "import sys\nsys.path.insert(0, %r)\n%s" % (str(_SCRIPTS), body),
        encoding="utf-8")
    return d


def _stage_policy(tmp_path, push_spec):
    p = tmp_path / "stage-policy.yaml"
    p.write_text(json.dumps({"stages": {"push": push_spec}}), encoding="utf-8")
    return p


def _guard_policy(tmp_path, preset):
    p = tmp_path / "guard-policy.yaml"
    p.write_text('schema_version: "1.0"\npreset: %s\noverrides: {}\n' % preset,
                 encoding="utf-8")
    return p


def _run(tmp_path, command, env_extra=None):
    env = dict(os.environ)
    for k in ("PYTEST_CURRENT_TEST", "HARNESS_HOOK_CONFIG", "HARNESS_ACTIVE_PLAN",
              "HARNESS_STAGE_POLICY", "HARNESS_GUARD_POLICY", "HARNESS_CHANGE_CLASS",
              "CI", "GITLAB_CI", "GITHUB_ACTIONS", "PYTHONPATH"):
        env.pop(k, None)
    env["HARNESS_ROOT"] = str(tmp_path)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_USER"] = "alice"
    for k, v in (env_extra or {}).items():
        env[k] = v
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(_GATE)], input=payload,
                          capture_output=True, text=True, env=env)


def _events(tmp_path):
    out = []
    trace = tmp_path / "state" / "trace"
    if trace.is_dir():
        for f in sorted(trace.glob("trace-*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                out.append(json.loads(line))
    return out


def _has_event(tmp_path, name):
    return any(e.get("event") == name for e in _events(tmp_path))


# 1 — a genuine evaluator crash FAILS CLOSED (a self-defect never silently
#     disables the gate) + traced ------------------------------------------------
def test_eval_crash_blocks(tmp_path):
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    inj = _inject(tmp_path,
                  "import artifact_check\n"
                  "def _boom(*a, **k):\n"
                  "    raise RuntimeError('injected eval crash')\n"
                  "artifact_check.evaluate_test_policy = _boom\n")
    proc = _run(tmp_path, "git push",
                {"HARNESS_CHANGE_CLASS": "feature", "PYTHONPATH": str(inj)})
    # A crash inside our OWN evaluator is a defect that would silently disable the
    # gate, so it fails CLOSED — routed through the test_policy_dod guard (block
    # under the default balanced preset). A wrapper crash fails closed earlier.
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert _has_event(tmp_path, "dod_eval_crash"), [e.get("event") for e in _events(tmp_path)]


# 2 — solo (require_plan:false) + no plan → PASS (solo posture untouched) -----
def test_no_plan_solo_passes(tmp_path):
    pol = _stage_policy(tmp_path, {"hard": True, "requires": [], "require_plan": False})
    proc = _run(tmp_path, "git push",
                {"HARNESS_STAGE_POLICY": str(pol), "HARNESS_CHANGE_CLASS": "feature"})
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)


# 3 — team (require_plan:true) + no plan → BLOCKED at Layer A -----------------
def test_no_plan_team_blocked_at_layerA(tmp_path):
    pol = _stage_policy(tmp_path, {"hard": True, "requires": ["verification"],
                                   "require_plan": True})
    proc = _run(tmp_path, "git push", {"HARNESS_STAGE_POLICY": str(pol)})
    # Personal-first: require_plan (Layer A) no-plan is advisory now, not a block.
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "[advisory]" in proc.stderr and "active plan" in proc.stderr


# 4 — a derivation crash does NOT block but DOES trace -----------------------
def test_derivation_throw_does_not_block_but_traces(tmp_path):
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    inj = _inject(tmp_path,
                  "import change_class_derivation as ccd\n"
                  "def _boom(*a, **k):\n"
                  "    raise RuntimeError('injected derivation crash')\n"
                  "ccd.derive_from_repo = _boom\n")
    proc = _run(tmp_path, "git push", {"PYTHONPATH": str(inj)})
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert _has_event(tmp_path, "dod_derivation_failed"), [e.get("event") for e in _events(tmp_path)]


# 5 — lowering the DoD guard below block leaves a guard_downgraded trace ------
def test_dod_downgrade_traced(tmp_path):
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    # bugfix requires regression too; it is missing → a real DoD FAIL to gate.
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    gp = _guard_policy(tmp_path, "lenient")
    proc = _run(tmp_path, "git push",
                {"HARNESS_GUARD_POLICY": str(gp), "HARNESS_CHANGE_CLASS": "bugfix"})
    # lenient → enforcement guard warns, not blocks → exit 0, but trace records it.
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert _has_event(tmp_path, "guard_downgraded"), [e.get("event") for e in _events(tmp_path)]


# 6 — a real hard DoD FAIL still blocks (the flip never loosened the gate) ----
def test_real_dod_fail_is_advisory(tmp_path):
    # Personal-first: a real DoD failure is advisory locally (exit 0), enforced remote.
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _FAIL_JUNIT)
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    proc = _run(tmp_path, "git push", {"HARNESS_CHANGE_CLASS": "feature"})
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "[advisory]" in proc.stderr


# 6 — the hard_stage_advisory knob (separate from soft_stage_advisory) ---------
def test_hard_stage_advisory_false_silences_but_traces(tmp_path):
    # hard_stage_advisory:false silences the [advisory] stderr line but the gate
    # still emits the gate_advisory TRACE and still does not block (exit 0).
    d = _mk_plan(tmp_path)  # plan exists but no verification artifact
    pol = tmp_path / "stage-policy.yaml"
    pol.write_text(json.dumps({
        "hard_stage_advisory": False,
        "stages": {"push": {"hard": True, "requires": ["verification"]}}}),
        encoding="utf-8")
    proc = _run(tmp_path, "git push", {"HARNESS_STAGE_POLICY": str(pol)})
    assert proc.returncode == 0, proc.stderr
    # the HARD-stage advisory line is silenced (its distinctive suffix is absent)…
    assert "presence enforcement lives in remote CI" not in proc.stderr
    # …but the gate_advisory TRACE is still emitted.
    assert "gate_advisory" in {e["event"] for e in _events(tmp_path)}


def test_soft_stage_advisory_unaffected_by_hard_knob(tmp_path):
    # The two knobs are independent: hard_stage_advisory:false does NOT silence a
    # SOFT-stage advisory (soft_stage_advisory governs that, defaulting on).
    pol = tmp_path / "stage-policy.yaml"
    pol.write_text(json.dumps({
        "hard_stage_advisory": False,
        "stages": {"push": {"hard": False, "requires": []}}}),
        encoding="utf-8")
    proc = _run(tmp_path, "git push", {"HARNESS_STAGE_POLICY": str(pol)})
    assert proc.returncode == 0
    assert "soft stage" in proc.stderr               # soft advisory still fires
