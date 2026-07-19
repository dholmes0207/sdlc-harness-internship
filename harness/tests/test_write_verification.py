"""test_write_verification.py — the deterministic verification write path.

write_verification.py writes a plan's canonical verification, drives the shared
snapshot + plan lifecycle, and self-verifies — in one run, so a Bash-issued
verification (which never trips the PostToolUse hook) still gets a per-phase
snapshot and an auto-closed plan. Contract paths go through subprocess with a
real HARNESS_ROOT seam; the snapshot-regression path is exercised in-process so
the snapshot can be forced to fail.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "harness" / "scripts" / "write_verification.py"
_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _d in (str(_SCRIPTS), str(_HOOKS)):
    if _d not in sys.path:
        sys.path.insert(0, _d)


# --- helpers ------------------------------------------------------------------

def _seed(root, name="plan", nodes=("p1",)):
    pdir = root / "plans" / name
    (pdir / "artifacts").mkdir(parents=True)
    (pdir / "plan.md").write_text("---\nstatus: pending\n---\n# x\n", encoding="utf-8")
    body = "subtasks:\n" + "".join(
        "  %s: {post: [verification-%s.json]}\n" % (n, n) for n in nodes)
    (pdir / "plan-graph.yaml").write_text(body, encoding="utf-8")
    return pdir


def _run(args, root, stdin=None, extra_env=None):
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(root)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *[str(a) for a in args]],
        input=stdin, capture_output=True, text=True, env=env)


def _snaps(pdir):
    return sorted(q.name for q in (pdir / "artifacts").glob("verification-*.json"))


# --- contract: write paths ----------------------------------------------------

def test_flag_build_writes_canonical_and_snapshot(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    r = _run([pdir, "--phase", "p1", "--stage", "ship", "--verdict", "PASS",
              "--check", "unit:PASS", "--check", "integration:PASS"], tmp_path)
    assert r.returncode == 0, r.stderr
    canon = json.loads((pdir / "artifacts" / "verification.json").read_text(encoding="utf-8"))
    assert canon["verdict"] == "PASS" and canon["phase"] == "p1"
    assert canon["actor"] and canon["ts"]
    assert [c["name"] for c in canon["checks"]] == ["unit", "integration"]
    assert (pdir / "artifacts" / "verification-p1.json").is_file()


def test_from_file_mode(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    rec = {"stage": "ship", "plan": "plan", "verdict": "PASS", "phase": "p1",
           "checks": [{"name": "unit", "status": "PASS"}]}
    f = tmp_path / "in.yaml"
    f.write_text(yaml.safe_dump(rec), encoding="utf-8")
    r = _run([pdir, "--from", f], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (pdir / "artifacts" / "verification-p1.json").is_file()
    canon = json.loads((pdir / "artifacts" / "verification.json").read_text(encoding="utf-8"))
    assert canon["actor"] and canon["ts"]  # filled in


def test_from_stdin(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    rec = {"stage": "ship", "plan": "plan", "verdict": "PASS", "phase": "p1",
           "checks": [{"name": "unit", "status": "PASS"}]}
    r = _run([pdir, "--from", "-"], tmp_path, stdin=json.dumps(rec))
    assert r.returncode == 0, r.stderr
    assert (pdir / "artifacts" / "verification-p1.json").is_file()


def test_script_drives_lifecycle(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1", "p2"])
    r1 = _run([pdir, "--phase", "p1", "--verdict", "PASS", "--check", "unit:PASS"], tmp_path)
    assert r1.returncode == 0, r1.stderr
    md = (pdir / "plan.md").read_text(encoding="utf-8")
    assert "status: in_progress" in md and "status: completed" not in md  # 1/2
    r2 = _run([pdir, "--phase", "p2", "--verdict", "PASS", "--check", "unit:PASS"], tmp_path)
    assert r2.returncode == 0, r2.stderr
    assert "status: completed" in (pdir / "plan.md").read_text(encoding="utf-8")  # 2/2


def test_stale_yaml_sibling_no_drift(tmp_path):
    """C1: a stale verification.yaml must not poison the snapshot — the script
    writes the file the resolver reads, so the snapshot is the NEW record."""
    pdir = _seed(tmp_path, nodes=["p3"])
    (pdir / "artifacts" / "verification.yaml").write_text(
        yaml.safe_dump({"stage": "ship", "plan": "plan", "verdict": "PASS",
                        "phase": "old", "checks": []}), encoding="utf-8")
    r = _run([pdir, "--phase", "p3", "--verdict", "PASS", "--check", "unit:PASS"], tmp_path)
    assert r.returncode == 0, r.stderr
    snap = json.loads((pdir / "artifacts" / "verification-p3.json").read_text(encoding="utf-8"))
    assert snap["phase"] == "p3"
    assert not (pdir / "artifacts" / "verification-old.json").exists()


def test_phase_must_be_graph_node(tmp_path):
    """M2: a phase that is not a plan-graph node is rejected before any write."""
    pdir = _seed(tmp_path, nodes=["p1"])
    r = _run([pdir, "--phase", "P1", "--verdict", "PASS", "--check", "unit:PASS"], tmp_path)
    assert r.returncode == 2
    assert "p1" in r.stderr  # valid nodes listed
    assert not (pdir / "artifacts" / "verification.json").exists()
    r2 = _run([pdir, "--phase", "p1", "--verdict", "PASS", "--check", "unit:PASS"], tmp_path)
    assert r2.returncode == 0, r2.stderr


def test_self_verify_content_mismatch_warns(tmp_path):
    """I2: a pre-existing snapshot with a different verdict means THIS write did
    not take — surfaced loudly, never a silent OK."""
    pdir = _seed(tmp_path, nodes=["p3"])
    (pdir / "artifacts" / "verification-p3.json").write_text(
        json.dumps({"stage": "ship", "plan": "plan", "verdict": "PASS_WITH_RISK",
                    "phase": "p3", "checks": []}), encoding="utf-8")
    r = _run([pdir, "--phase", "p3", "--verdict", "PASS", "--check", "unit:PASS"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "did NOT take" in r.stderr


def test_blocked_no_snapshot_exit_ok(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    r = _run([pdir, "--phase", "p1", "--verdict", "BLOCKED", "--check", "unit:FAIL"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (pdir / "artifacts" / "verification.json").exists()
    assert _snaps(pdir) == []


def test_cross_plan_binding(tmp_path):
    """A record whose plan field points elsewhere must not drive THIS plan's
    lifecycle (no closing the wrong plan)."""
    pdir = _seed(tmp_path, nodes=["p1"])
    rec = {"stage": "ship", "plan": "some-other-plan", "verdict": "PASS",
           "phase": "p1", "checks": [{"name": "unit", "status": "PASS"}]}
    f = tmp_path / "in.json"
    f.write_text(json.dumps(rec), encoding="utf-8")
    r = _run([pdir, "--from", f], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "status: completed" not in (pdir / "plan.md").read_text(encoding="utf-8")


def test_missing_args_exit_2(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    assert _run([pdir, "--phase", "p1"], tmp_path).returncode == 2  # no verdict
    assert _run([pdir], tmp_path).returncode == 2  # nothing


# --- p3: check-name validation ramp (off | soft | hard) -----------------------

_SHIPPED_TP = _ROOT / "harness" / "data" / "test-policy.yaml"


def _policy_file(tmp_path, mode, name="tp.yaml"):
    """A valid tier-1 policy derived from the shipped one, with the validation
    knob set (mode=None drops the key to exercise the missing-key default)."""
    pol = yaml.safe_load(_SHIPPED_TP.read_text(encoding="utf-8"))
    if mode is None:
        pol.pop("check_name_validation", None)
    else:
        pol["check_name_validation"] = mode
    p = tmp_path / name
    p.write_text(yaml.safe_dump(pol), encoding="utf-8")
    return p


def test_default_is_soft(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    pol = _policy_file(tmp_path, None)  # no key -> defaults to soft
    r = _run([pdir, "--phase", "p1", "--verdict", "PASS", "--check", "pytest-unit:PASS"],
             tmp_path, extra_env={"HARNESS_TEST_POLICY": str(pol)})
    assert r.returncode == 0, r.stderr
    assert "pytest-unit" in r.stderr  # advisory names the offender
    assert (pdir / "artifacts" / "verification-p1.json").is_file()  # still wrote


def test_soft_wording_is_sharp(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    pol = _policy_file(tmp_path, "soft")
    r = _run([pdir, "--phase", "p1", "--verdict", "PASS", "--check", "pytest-unit:PASS"],
             tmp_path, extra_env={"HARNESS_TEST_POLICY": str(pol)})
    assert r.returncode == 0, r.stderr
    for kw in ("DoD", "ship rớt", "Sửa tên ngay"):
        assert kw in r.stderr, kw
    assert "unit" in r.stderr and "integration" in r.stderr  # valid set listed


def test_hard_refuses_unknown(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    pol = _policy_file(tmp_path, "hard")
    r = _run([pdir, "--phase", "p1", "--verdict", "PASS", "--check", "pytest-unit:PASS"],
             tmp_path, extra_env={"HARNESS_TEST_POLICY": str(pol)})
    assert r.returncode != 0
    assert not (pdir / "artifacts" / "verification.json").exists()
    assert _snaps(pdir) == []


def test_hard_passes_valid(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    pol = _policy_file(tmp_path, "hard")
    r = _run([pdir, "--phase", "p1", "--verdict", "PASS", "--check", "unit:PASS"],
             tmp_path, extra_env={"HARNESS_TEST_POLICY": str(pol)})
    assert r.returncode == 0, r.stderr
    assert (pdir / "artifacts" / "verification-p1.json").is_file()


def test_off_skips_validation(tmp_path):
    pdir = _seed(tmp_path, nodes=["p1"])
    pol = _policy_file(tmp_path, "off")
    r = _run([pdir, "--phase", "p1", "--verdict", "PASS", "--check", "pytest-unit:PASS"],
             tmp_path, extra_env={"HARNESS_TEST_POLICY": str(pol)})
    assert r.returncode == 0, r.stderr
    assert "DoD" not in r.stderr  # no check-name advisory
    assert (pdir / "artifacts" / "verification-p1.json").is_file()


def test_dev_override_flips_mode(tmp_path):
    pdir = _seed(tmp_path, "plan", ["p1"])
    soft = _policy_file(tmp_path, "soft", "soft.yaml")
    hard = _policy_file(tmp_path, "hard", "hard.yaml")
    r_soft = _run([pdir, "--phase", "p1", "--verdict", "PASS", "--check", "pytest-unit:PASS"],
                  tmp_path, extra_env={"HARNESS_TEST_POLICY": str(soft)})
    assert r_soft.returncode == 0, r_soft.stderr
    # fresh plan for the hard run (the soft run already wrote the canonical)
    pdir2 = _seed(tmp_path, "plan2", ["p1"])
    r_hard = _run([pdir2, "--phase", "p1", "--verdict", "PASS", "--check", "pytest-unit:PASS"],
                  tmp_path, extra_env={"HARNESS_TEST_POLICY": str(hard)})
    assert r_hard.returncode != 0

def test_read_from_malformed_dies_friendly(tmp_path):
    import write_verification as wv
    bad = tmp_path / "bad.json"
    bad.write_text("{unterminated", encoding="utf-8")
    with pytest.raises(SystemExit):
        wv._read_from(str(bad))
