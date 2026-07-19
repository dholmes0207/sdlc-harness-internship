"""test_simplify_gate.py — the ship/pr/deploy diff-simplification gate.

Posture (user-locked 2026-06-20): default ON + SOFT (warn), escalatable to HARD
(block), thresholds + exclusions human-only/agent-locked. The gate reads mode +
thresholds + exclusions ONLY from the write_guarded harness/data/simplify-policy.yaml
(no HARNESS_SIMPLIFY_* env knob), fires only at the pr/ship/deploy human-checkpoint
stages, excludes generated files (manifest/lockfiles) so the harness never self-blocks,
and fails OPEN on its own internal errors (a crashing heuristic must not block a ship).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_GATE = _HOOKS / "simplify_gate.py"
for _p in (_HOOKS, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ---- helpers ----------------------------------------------------------------

def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, text=True,
                          capture_output=True).stdout


def _git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "base.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _write_policy(root: Path, **over) -> None:
    data = {"schema_version": "1.0", "mode": "warn",
            "stages": ["pr", "ship", "deploy"],
            "thresholds": {"loc_delta": 400, "file_count": 8, "single_file_loc": 200},
            "exclude": ["**/manifest.json", "*.lock"]}
    data.update(over)
    d = root / "harness" / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "simplify-policy.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _payload(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                       "session_id": "s1"})


# ---- policy resolution ------------------------------------------------------

def test_policy_defaults_warn_when_file_absent(tmp_path):
    import simplify_gate
    pol = simplify_gate.resolve_policy(tmp_path)
    assert pol["mode"] == "warn"                       # default ON + SOFT
    assert pol["thresholds"]["loc_delta"] > 0
    assert "pr" in pol["stages"] and "commit" not in pol["stages"]


def test_policy_read_from_guarded_yaml(tmp_path):
    import simplify_gate
    _write_policy(tmp_path, mode="block")
    assert simplify_gate.resolve_policy(tmp_path)["mode"] == "block"


def test_env_does_not_override_mode(tmp_path, monkeypatch):
    # Agent-lock: there is NO HARNESS_SIMPLIFY_* policy knob; mode comes only
    # from the guarded file. An agent setting any such env must not move it.
    import simplify_gate
    _write_policy(tmp_path, mode="warn")
    monkeypatch.setenv("HARNESS_SIMPLIFY_MODE", "off")
    monkeypatch.setenv("HARNESS_SIMPLIFY_GATE", "off")
    assert simplify_gate.resolve_policy(tmp_path)["mode"] == "warn"


# ---- signal + evaluation ----------------------------------------------------

def test_evaluate_trips_each_threshold():
    import simplify_gate
    th = {"loc_delta": 400, "file_count": 8, "single_file_loc": 200}
    assert simplify_gate.evaluate({"total_loc": 401, "file_count": 1, "max_file_loc": 1}, th)
    assert simplify_gate.evaluate({"total_loc": 1, "file_count": 9, "max_file_loc": 1}, th)
    assert simplify_gate.evaluate({"total_loc": 1, "file_count": 1, "max_file_loc": 201}, th)
    assert not simplify_gate.evaluate({"total_loc": 1, "file_count": 1, "max_file_loc": 1}, th)


def test_exclude_drops_generated_files(tmp_path):
    import simplify_gate
    root = _git_repo(tmp_path)
    # a huge generated file (excluded) + a tiny real change (counted)
    (root / "manifest.json").write_text("\n".join(str(i) for i in range(5000)), encoding="utf-8")
    (root / "base.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    sig = simplify_gate.compute_signals(root, ["**/manifest.json", "*.lock"])
    assert all("manifest.json" not in f for f in sig["files"])
    assert sig["total_loc"] < 100                      # manifest not counted


# ---- core contract ----------------------------------------------------------

def _big_diff(root: Path) -> None:
    (root / "big.py").write_text("\n".join("a = %d" % i for i in range(450)), encoding="utf-8")


def test_core_warn_does_not_block(tmp_path, monkeypatch):
    import simplify_gate
    root = _git_repo(tmp_path)
    _write_policy(root, mode="warn")
    _big_diff(root)
    monkeypatch.setenv("HARNESS_ROOT", str(root))
    assert simplify_gate.core(json.loads(_payload("gh release create v1"))) is None


def test_core_block_blocks_on_ship(tmp_path, monkeypatch):
    import simplify_gate
    root = _git_repo(tmp_path)
    _write_policy(root, mode="block")
    _big_diff(root)
    monkeypatch.setenv("HARNESS_ROOT", str(root))
    reason = simplify_gate.core(json.loads(_payload("gh release create v1")))
    assert reason and "simplif" in reason.lower()


def test_core_ignores_non_ship_stage(tmp_path, monkeypatch):
    import simplify_gate
    root = _git_repo(tmp_path)
    _write_policy(root, mode="block")
    _big_diff(root)
    monkeypatch.setenv("HARNESS_ROOT", str(root))
    # plain commit / unrelated command is not a pr/ship/deploy checkpoint
    assert simplify_gate.core(json.loads(_payload("git commit -m x"))) is None
    assert simplify_gate.core(json.loads(_payload("ls -la"))) is None


def test_core_off_is_inert(tmp_path, monkeypatch):
    import simplify_gate
    root = _git_repo(tmp_path)
    _write_policy(root, mode="off")
    _big_diff(root)
    monkeypatch.setenv("HARNESS_ROOT", str(root))
    assert simplify_gate.core(json.loads(_payload("gh release create v1"))) is None


# ---- subprocess: real exit contract ----------------------------------------

def _run(root: Path, command: str, env_extra=None):
    env = dict(os.environ, HARNESS_ROOT=str(root))
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(_GATE)], input=_payload(command),
                          text=True, capture_output=True, env=env)


def test_subprocess_block_exits_2(tmp_path):
    root = _git_repo(tmp_path)
    _write_policy(root, mode="block")
    _big_diff(root)
    r = _run(root, "gh release create v1")
    assert r.returncode == 2
    assert "simplif" in r.stderr.lower()


def test_subprocess_warn_continues(tmp_path):
    root = _git_repo(tmp_path)
    _write_policy(root, mode="warn")
    _big_diff(root)
    r = _run(root, "gh release create v1")
    assert r.returncode == 0
    assert json.loads(r.stdout).get("continue") is True


def test_subprocess_fail_open_on_garbage_payload(tmp_path):
    # internal trouble (no git repo / odd payload) must never block a ship
    root = tmp_path / "nogit"
    root.mkdir()
    r = subprocess.run([sys.executable, str(_GATE)], input="not json",
                       text=True, capture_output=True,
                       env=dict(os.environ, HARNESS_ROOT=str(root)))
    assert r.returncode == 0
