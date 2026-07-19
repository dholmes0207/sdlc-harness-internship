"""test_dispatch_wiring_parity.py — a migrated (event, matcher) group loses no hook.

When a group's per-hook commands are replaced by ONE hook_dispatch.py command, every
former hook must reappear as a core in hook-dispatch.yaml — a dropped core is a
silently disabled gate. These tests hold that invariant structurally:
  - every dispatcher-wired group has a non-empty core list in the registry;
  - no individual hook entry shares an (event, matcher) with a dispatcher entry (no
    stranded hook that would double-run or bypass the dispatcher);
  - the registry cores for a migrated group match the escape-hatch snapshot of the
    old wiring exactly (no core added or dropped vs what was replaced).
"""
import re
import sys
from pathlib import Path

import yaml

_HARNESS = Path(__file__).resolve().parent.parent
_REG = _HARNESS / "install" / "hooks-registration.yaml"
_DISP = _HARNESS / "data" / "hook-dispatch.yaml"


def _registration():
    return yaml.safe_load(_REG.read_text(encoding="utf-8"))["hooks"]


def _registry_groups():
    return yaml.safe_load(_DISP.read_text(encoding="utf-8"))["groups"]


def _cmd_basename(entry):
    m = re.search(r"/hooks/([A-Za-z0-9_]+)\.py", entry.get("command", ""))
    return m.group(1) if m else None


def _key(entry):
    return (entry.get("event"), entry.get("matcher"))


def _dispatcher_wired_keys():
    return {_key(e) for e in _registration() if _cmd_basename(e) == "hook_dispatch"}


def _registry_key(group_key):
    event, _, matcher = str(group_key).partition(":")
    return (event, matcher or None)


class TestParity:
    def test_every_dispatcher_group_has_registry_cores(self):
        reg = _registry_groups()
        reg_keys = {_registry_key(k) for k in reg}
        for key in _dispatcher_wired_keys():
            # normalise a missing matcher to None on both sides
            norm = (key[0], key[1])
            assert norm in reg_keys, \
                "dispatcher wired for %s but hook-dispatch.yaml has no cores" % (norm,)
        for gk, cores in reg.items():
            assert cores, "empty core list for %s" % gk

    def test_no_individual_hook_in_a_dispatcher_group(self):
        wired = _dispatcher_wired_keys()
        for e in _registration():
            bn = _cmd_basename(e)
            if bn == "hook_dispatch":
                continue
            assert _key(e) not in wired, (
                "%s is individually wired in %s which is also dispatcher-wired "
                "(double-run / bypass)" % (bn, _key(e)))

    def test_registry_matches_escape_hatch_snapshots(self):
        # the plan's escape-hatch artifacts record the exact old per-group wiring;
        # every module they name must be a registry core for that group (no drop),
        # and the registry adds none the snapshot did not have (no accidental core).
        plans = _HARNESS.parent / "plans" / "260710-1047-harness-hook-dispatch-observability" / "artifacts"
        snaps = sorted(plans.glob("old-wiring-*.yaml")) if plans.is_dir() else []
        if not snaps:
            import pytest
            pytest.skip("escape-hatch snapshots not present in this checkout")
        reg = _registry_groups()
        # map registry (event, matcher-or-None) -> set of modules
        reg_mods = {}
        for gk, cores in reg.items():
            ev, _, mt = str(gk).partition(":")
            reg_mods[(ev, mt or None)] = {c["module"] for c in cores}
        for snap in snaps:
            text = snap.read_text(encoding="utf-8")
            ev = re.search(r"event:\s*(\S+)", text).group(1)
            mt = re.search(r"matcher:\s*(\S+)", text)
            mt = mt.group(1) if mt else None
            old_mods = set(re.findall(r"/hooks/([A-Za-z0-9_]+)\.py", text))
            key = (ev, mt)
            assert key in reg_mods, "no registry group for snapshot %s" % (key,)
            missing = old_mods - reg_mods[key]
            assert not missing, "%s: old hooks dropped from registry: %s" % (key, missing)
            # both directions: the registry must not add a core the old wiring never had
            # (an accidental extra gate in a migrated group is a silent expansion).
            extra = reg_mods[key] - old_mods
            assert not extra, "%s: registry added cores absent from the old wiring: %s" % (key, extra)
