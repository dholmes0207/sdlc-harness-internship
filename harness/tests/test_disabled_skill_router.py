"""Subprocess exit-code contract for disabled_skill_router (PreToolUse:Skill).

The block-with-map decision is proven by running the real hook as a subprocess over
stdin JSON — never by importing internals, which would not measure the exit-2 contract
the host sees. Adversarial anchors:
  - a Skill call whose target is install-disabled (omitted) → exit 2 + a reason carrying
    ALL THREE recovery paths (/hs:use, the stash abs path, the --enable command).
  - a live or unknown target → exit 0 silent (the router must never brick a normal call).
  - garbage stdin and a corrupt omit record → exit 0 (fail-open deviation: a broken
    disabled-state source must not wedge every skill invocation).
  - slug normalization: "hs:ask", "/hs:ask", "ask" all resolve to the same skill.

Whether PreToolUse(Skill) actually FIRES for an omitted skill is the separate live probe
deferred to P3 (host may validate the enum-catalog before the hook). This suite proves
the hook LOGIC is correct when it does fire; the 3-path map also reaches the model via
disabled_ref_nudge + hs:use, so the deliverable never rests on the router alone.
"""
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _ROOT / "harness" / "hooks" / "disabled_skill_router.py"


def _root_with_disabled(base, skill="ask", live=("cook",), corrupt=False):
    """A project root where `skill` is install-disabled and each of `live` is present.

    Normally the skill is BOTH recorded and stashed (the stock disable). corrupt=True
    writes an unparseable omit record AND omits the stash, so the record is the only
    disabled signal — a broken record must then read as "not disabled" (fail-open)."""
    skills = base / "harness/plugins/hs/skills"
    stash = base / "harness/plugins/hs/disabled-skills"
    for s in live:
        (skills / s).mkdir(parents=True, exist_ok=True)
        (skills / s / "SKILL.md").write_text("---\nname: hs:%s\n---\n# %s\n" % (s, s))
    if not corrupt:
        (stash / skill).mkdir(parents=True, exist_ok=True)
        (stash / skill / "SKILL.md").write_text(
            "---\nname: hs:%s\n---\n# %s\n" % (skill, skill))
    rec = base / "harness/state/install-omitted-skills.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text("{{{ not json" if corrupt else json.dumps({"omitted": [skill]}))
    return base


def _run(payload, root):
    env = {
        "HARNESS_HOOK_AUDIT_DISABLED": "1",
        "CLAUDE_PROJECT_DIR": str(root),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    return subprocess.run(
        [sys.executable, str(_HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, env=env)


def _skill(name):
    return {"tool_name": "Skill", "tool_input": {"skill": name}, "session_id": "s1"}


def test_blocks_omitted_target_exit2_with_three_paths(tmp_path):
    root = _root_with_disabled(tmp_path)
    p = _run(_skill("hs:ask"), root)
    assert p.returncode == 2, p.stderr
    assert "/hs:use" in p.stderr
    assert "--enable" in p.stderr
    # the stash abs path (read-inline route) is named
    assert "disabled-skills" in p.stderr and "ask" in p.stderr


def test_live_target_exit0_silent(tmp_path):
    root = _root_with_disabled(tmp_path)
    p = _run(_skill("hs:cook"), root)
    assert p.returncode == 0, p.stderr


def test_unknown_target_exit0(tmp_path):
    root = _root_with_disabled(tmp_path)
    p = _run(_skill("hs:nosuchskill"), root)
    assert p.returncode == 0, p.stderr


def test_garbage_stdin_exit0(tmp_path):
    root = _root_with_disabled(tmp_path)
    env = {
        "HARNESS_HOOK_AUDIT_DISABLED": "1",
        "CLAUDE_PROJECT_DIR": str(root),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    p = subprocess.run([sys.executable, str(_HOOK)], input="not json at all",
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr


def test_corrupt_record_fails_open(tmp_path):
    # A broken disabled-state source must not block a skill that WOULD read as disabled
    # if the record parsed — the router fails open rather than wedging every Skill call.
    root = _root_with_disabled(tmp_path, corrupt=True)
    p = _run(_skill("hs:ask"), root)
    assert p.returncode == 0, p.stderr


def test_slug_normalization(tmp_path):
    root = _root_with_disabled(tmp_path, skill="ask")
    for form in ("hs:ask", "/hs:ask", "ask"):
        p = _run(_skill(form), root)
        assert p.returncode == 2, "form %r should block: %s" % (form, p.stderr)


def _run_with_state(payload, root, state_dir, extra_env=None):
    env = {
        "HARNESS_HOOK_AUDIT_DISABLED": "1",
        "CLAUDE_PROJECT_DIR": str(root),
        "HARNESS_STATE_DIR": str(state_dir),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, env=env)


def test_block_emits_demand_failopen(tmp_path):
    # The block STILL exits 2, but a demand row (via:router_block) is appended as a
    # secondary re-enable signal — keyed on the target, fail-open, OUTSIDE the exit-2.
    root = _root_with_disabled(tmp_path, skill="ask")
    state = tmp_path / "state"
    p = _run_with_state(_skill("hs:ask"), root, state)
    assert p.returncode == 2, p.stderr
    # Union legacy sink + per-session shards (P2 shards a demand row carrying a
    # resolved session to invocations-<disc>.jsonl).
    tdir = state / "telemetry"
    row_files = sorted(f for f in tdir.glob("invocations*.jsonl")
                       if not f.name.endswith(".bak")) if tdir.is_dir() else []
    assert row_files, "router must append a demand row"
    rows = [json.loads(x) for f in row_files
            for x in f.read_text().splitlines() if x.strip()]
    demand = [r for r in rows if r.get("via") == "router_block"]
    assert demand and demand[0]["skill"] in ("ask", "hs:ask")
    assert demand[0].get("proxy_invoked") is True

    # Even if the emitter itself is broken, the block still fires (fail-open never
    # swallows the block): point HARNESS_STATE_DIR at an un-writable file location.
    dead = tmp_path / "deadfile"
    dead.write_text("x")
    p2 = _run_with_state(_skill("hs:ask"), root, dead)
    assert p2.returncode == 2, "a broken emit must not turn the block dark: %s" % p2.stderr
