"""End-to-end off-skill slice: disable -> use-from-stash -> demand -> re-enable.

The whole off-skill machinery (hs_cli dir-omit, disabled_skills query, the hs:use
proxy's demand emit, lens re-enable surfacing, the disabled_ref nudge, the
disabled_skill router) is exercised on a git-seeded temp-install so the run cannot
touch the real repo tree. This is the automatable half of the phase's live verify;
the fresh-session M1 probe (does PreToolUse(Skill) even fire for an omitted name)
is the manual half recorded in the phase artifact.
"""
import sys
from pathlib import Path

_E2E = Path(__file__).resolve().parent.parent / "e2e"
if str(_E2E) not in sys.path:
    sys.path.insert(0, str(_E2E))

import run_offskill_slice as rs  # noqa: E402


def test_disable_use_demand_enable_roundtrip(tmp_path):
    root = rs.build_install(tmp_path / "proj")
    summary = rs.run_slice(root)
    # every mechanical link in the loop must hold, not just the overall verdict
    for key in ("disable_moved_to_stash", "git_rename_not_deletion", "list_shows_off",
                "status_disabled", "chain_closure", "stash_prose_readable",
                "demand_rows_target_keyed", "lens_surfaces_demand",
                "nudge_fires_with_use", "router_blocks_with_map", "enable_roundtrip"):
        assert summary.get(key), "%s failed: %r" % (key, summary)
    assert summary["ok"], summary


def test_enable_roundtrip_leaves_no_residue(tmp_path):
    root = rs.build_install(tmp_path / "proj")
    rs.run_slice(root)
    plug = root / "harness" / "plugins" / "hs"
    # enabling the leaf restores the whole transitive chain (leaf -> mid -> dep)
    # back into skills/ with no orphan dir left in the stash — a clean round-trip
    for name in ("demo-leaf", "demo-mid", "demo-dep"):
        assert not (plug / "disabled-skills" / name).exists(), name
        assert (plug / "skills" / name / "SKILL.md").is_file(), name
