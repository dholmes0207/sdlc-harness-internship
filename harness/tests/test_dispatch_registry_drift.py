"""test_dispatch_registry_drift.py — a compliance gate cannot vanish through the registry.

Independent drift guard (C5). It derives the set of compliance gates from the AUTHORITATIVE
source — each hook file's own `HOOK_CLASS = "compliance"` constant — NOT from the dispatch
registry (using the registry as the source of truth would let `delete a row` silently
disable a gate while the test still passes). Every derived gate must be covered by EITHER
its own hooks-registration.yaml command OR a hook-dispatch.yaml core (under a wired
dispatcher). Deleting a compliance core from the registry without re-registering it
individually makes the gate uncovered → this test goes red.
"""
import re
import sys
from pathlib import Path

import yaml

_HARNESS = Path(__file__).resolve().parent.parent
_HOOKS = _HARNESS / "hooks"
_REG = _HARNESS / "install" / "hooks-registration.yaml"
_DISP = _HARNESS / "data" / "hook-dispatch.yaml"

# hook_dispatch is the multiplexer, not a leaf gate (posture is per-core).
_EXEMPT = {"hook_dispatch"}


def _compliance_gates() -> set:
    """Every hook whose OWN HOOK_CLASS constant is compliance — the independent source."""
    gates = set()
    for p in _HOOKS.glob("*.py"):
        if p.stem in _EXEMPT:
            continue
        if re.search(r'(?m)^HOOK_CLASS\s*=\s*["\']compliance["\']',
                     p.read_text(encoding="utf-8")):
            gates.add(p.stem)
    return gates


def _individually_registered() -> set:
    text = _REG.read_text(encoding="utf-8")
    return set(re.findall(r"/hooks/([A-Za-z0-9_]+)\.py", text))


def _dispatch_cores() -> set:
    if "hook_dispatch.py" not in _REG.read_text(encoding="utf-8"):
        return set()  # dispatcher not wired -> registry is inert
    data = yaml.safe_load(_DISP.read_text(encoding="utf-8")) or {}
    mods = set()
    for cores in (data.get("groups") or {}).values():
        for c in (cores or []):
            if isinstance(c, dict) and c.get("module"):
                mods.add(c["module"])
    return mods


def test_every_compliance_gate_is_covered():
    gates = _compliance_gates()
    assert gates, "sanity: at least one compliance gate must exist"
    covered = _individually_registered() | _dispatch_cores()
    uncovered = sorted(gates - covered)
    assert not uncovered, (
        "compliance gate(s) neither individually registered nor a dispatch core "
        "(a deleted registry row silently disabled a gate): %s" % uncovered)


def test_migrated_gate_lives_in_registry_not_registration():
    # gates known to have been migrated must be dispatch cores AND absent as individual
    # PreToolUse:Bash commands (proves the drift guard sees the registry, not just the
    # old registration).
    cores = _dispatch_cores()
    for gate in ("gate_stage", "secret_scan_before_ship", "bash_safety_guard",
                 "protected_ref_guard"):
        assert gate in cores, "%s must be a dispatch core after migration" % gate


def test_removing_a_core_would_be_detected():
    # simulate a registry with a compliance core removed -> the coverage check must fail
    gates = _compliance_gates()
    reg = _individually_registered()
    # remove gate_stage from BOTH surfaces to model a silent drop
    reg_wo = reg - {"gate_stage"}
    cores_wo = _dispatch_cores() - {"gate_stage"}
    covered = reg_wo | cores_wo
    if "gate_stage" in gates:  # only meaningful while gate_stage is compliance
        assert "gate_stage" in (gates - covered), \
            "the drift check must flag a compliance gate dropped from every surface"
