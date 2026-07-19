"""Post-teardown invariants: the whitelist overlay mechanism is gone from code+config,
the new deny-list cage files are on GUARD_LIST + in a component, and the cross-path
floor holds (core-immune, harness binary, the audit store, and the goal path)."""
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = _REPO / "harness" / "scripts"
_HOOKS = _REPO / "harness" / "hooks"
for _p in (_SCRIPTS, _HOOKS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import write_guard  # noqa: E402
import floor_bash_guard as fbg  # noqa: E402

# assembled so THIS test file is never itself a hit of the scan below
_OVERLAY_ENV = "HARNESS_AGENT_PERMISSIONS" + "_OVERLAY"


def test_no_overlay_env_in_code_or_config():
    hits = []
    roots = [_SCRIPTS, _HOOKS, _REPO / "harness" / "data", _REPO / "config"]
    for root in roots:
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".yaml", ".yml", ".json"):
                try:
                    if _OVERLAY_ENV in f.read_text(encoding="utf-8"):
                        hits.append(str(f.relative_to(_REPO)))
                except (OSError, UnicodeDecodeError):
                    continue
    settings = _REPO / ".claude" / "settings.local.json"
    if settings.is_file() and _OVERLAY_ENV in settings.read_text(encoding="utf-8"):
        hits.append(".claude/settings.local.json")
    assert hits == [], "overlay mechanism still referenced in code/config: %s" % hits


def test_overlay_file_deleted():
    assert not (_REPO / "config" / "agent-permissions.overlay.yaml").exists()


def test_whitelist_table_is_inert():
    tbl = yaml.safe_load((_REPO / "harness" / "data" / "agent-permissions.yaml").read_text(encoding="utf-8"))
    assert (tbl or {}).get("roles") in (None, {}), "the retired whitelist table must be inert"


def test_guard_list_covers_deny_modules():
    needed = {
        "harness/scripts/write_deny_policy.py",
        "harness/scripts/deny_matcher.py",
        "harness/scripts/deny_audit.py",
        "harness/data/write-deny-policy.yaml",
        "harness/schemas/write-deny-policy.json",
    }
    assert needed <= set(write_guard.GUARD_LIST)


def test_component_registers_write_deny():
    comps = yaml.safe_load((_REPO / "harness" / "data" / "components.yaml").read_text(encoding="utf-8"))["components"]
    wd = comps["write-deny"]
    assert "floor_bash_guard" in wd["hooks"]
    assert {"write_deny_policy", "deny_matcher", "deny_audit"} <= set(wd["scripts"])
    assert "write-deny-policy.yaml" in wd["data"]


def _bash(cmd):
    return fbg.core({"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "t"})


def test_cross_path_floor_holds():
    # C4: the audit store is not carved -> a shell rewrite of the trail is blocked
    assert _bash("sed -i s/x/y/ harness/state/trace/trace-x.jsonl")
    # C1: an obfuscation-wrapped write into the guard code is blocked
    assert _bash('eval "echo x > harness/hooks/agent_rbac_guard.py"')
    # goal: plain source out of the box passes
    assert _bash("echo x > src/app.tsx") is None
