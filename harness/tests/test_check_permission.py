"""Tests for check_permission.py — the self-service deny-list write-scope reporter."""
import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import check_permission as cp  # noqa: E402


def test_subagent_scope_reports_hard_floor():
    scope = cp.resolve("developer")
    assert scope["unrestricted"] is False
    hard = scope["hard_floor"]
    assert "harness/**" in hard                       # the harness binary
    assert any(g.startswith("harness/hooks") for g in hard)  # core-immune guard code
    assert "harness/tests/**" in scope["hard_binary_carve"]


def test_namespaced_parent_unrestricted():
    assert cp.resolve("hs:_parent")["unrestricted"] is True


def test_parent_unrestricted():
    assert cp.resolve("_parent")["unrestricted"] is True


def test_soft_rules_reported(tmp_path, monkeypatch):
    pol = tmp_path / "wdp.yaml"
    pol.write_text("soft_rules:\n  - deny: 'build/**'\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_WRITE_DENY_POLICY", str(pol))
    scope = cp.resolve("developer")
    assert {"deny": "build/**"} in scope["soft_rules"]


def test_cli_prints_floor_and_rule():
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_permission.py"), "--name", "developer"],
        capture_output=True, text=True, env={**os.environ})
    assert proc.returncode == 0
    assert "harness/**" in proc.stdout
    assert "BLOCKED" in proc.stdout and "caged" in proc.stdout


def test_cli_json():
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_permission.py"), "--name", "developer", "--json"],
        capture_output=True, text=True, env={**os.environ})
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["role"] == "developer"
    assert data["unrestricted"] is False
    assert "harness/**" in data["hard_floor"]
