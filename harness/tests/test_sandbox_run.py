"""Contract for sandbox_run.py -- the R9 layer 2-5 jail (plan phase 13).

Pins: the layered jail actually runs a fill and scores it (denylist refuse
BEFORE execution, env scrub, no-network preamble, per-case timeout with
whole-tree kill, OS containment via bwrap when available with an honest
python-filter fallback + a hard containment_error when bwrap is demanded but
missing), the parent-side canonical comparator (never the child) that closes
the lying-__eq__/forged-serializer exploits, the mandatory edge-case set,
and the evidence artifact's stable shape + exit-code contract (0/1/2/3/4).
"""

import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys

import pytest

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = (
    _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "sandbox_run.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("sandbox_run", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(args, env_overrides=None, timeout=60):
    env = dict(os.environ)
    env.pop("HARNESS_R9_CONTAINMENT", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)] + list(args),
        capture_output=True, text=True, env=env, timeout=timeout,
    )


def _write_fill(tmp_path, source, name="fill.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _write_config(tmp_path, case_matrix, name="config.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"case_matrix": case_matrix}), encoding="utf-8")
    return path


_ROBUST_KV_FILL = '''
def run_pipeline(input_data):
    out = {}
    if not isinstance(input_data, str):
        return out
    for line in input_data.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out
'''


def test_clean_fill_all_pass(tmp_path):
    fill = _write_fill(tmp_path, _ROBUST_KV_FILL)
    config = _write_config(tmp_path, [
        {"case": "c1", "input": "name: x", "expect": {"name": "x"}},
        {"case": "c2", "input": "a: 1\nb: 2", "expect": {"a": "1", "b": "2"}},
    ])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run_pipeline",
                   "--config", str(config), "--evidence-out", str(evidence_out),
                   "--case-timeout", "5"], env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert len(evidence["cases"]) == 2
    assert len(evidence["edge_cases"]) == 5
    assert evidence["summary"] == {"total": 7, "pass": 7, "fail": 0}
    expected_hash = "sha256:" + __import__("hashlib").sha256(config.read_bytes()).hexdigest()
    assert evidence["card_hash"] == expected_hash


def test_denylist_refuses_before_execute(tmp_path):
    marker = tmp_path / "should-not-exist.txt"
    fill_src = (
        "import socket\n\n"
        "def run(x):\n"
        "    with open(%r, 'w') as fh:\n"
        "        fh.write('ran')\n"
        "    return x\n" % str(marker)
    )
    fill = _write_fill(tmp_path, fill_src)
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)])

    assert result.returncode == 3, result.stdout + result.stderr
    assert not marker.exists()
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert evidence["denylist"]["clean"] is False
    assert any(f["code"] == "network" for f in evidence["denylist"]["findings"])


def test_fork_grandchild_killed(tmp_path):
    marker = tmp_path / "fork-marker-should-not-exist.txt"
    fill_src = (
        "import os\n\n"
        "def run(x):\n"
        "    marker = %r\n"
        "    with open(marker, 'w') as fh:\n"
        "        fh.write('ran')\n"
        "    os.fork()\n"
        "    return 'ok'\n" % str(marker)
    )
    fill = _write_fill(tmp_path, fill_src)
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)])

    # Two-tier defense: os.fork is a denylist hit (layer 1) -- the fill never
    # executes, so its grandchild-spawning attempt never happens either. If
    # a future denylist evasion ever let this through, killpg (layer 5) is
    # the second tier; either way the marker must never appear.
    assert result.returncode == 3, result.stdout + result.stderr
    assert not marker.exists()


def test_env_scrubbed():
    mod = _load()
    env = mod._build_env(Path("/tmp/some-sandbox"))
    assert set(env.keys()) <= mod.ENV_WHITELIST
    assert "PATH" in env


def test_network_blocked(tmp_path):
    fill = _write_fill(tmp_path, "def run(x):\n    return x\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out), "--probe-network"],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE_NETWORK_BLOCKED" in result.stdout


def test_socket_and__socket_blocked(tmp_path):
    """Runs the driver script directly (bypassing sandbox_run's wiring
    entirely) to prove the preamble itself blocks socket.socket,
    socket.getaddrinfo, AND _socket.socket."""
    mod = _load()
    driver_dir = tmp_path / "driver_only"
    driver_dir.mkdir()
    (driver_dir / "driver.py").write_text(mod._DRIVER_SOURCE, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "driver.py", "__x__", "__x__", "__x__", "__x__", "1"],
        cwd=str(driver_dir), capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE_NETWORK_BLOCKED" in result.stdout


def test_timeout_isolated_per_case(tmp_path):
    fill_src = (
        "def run(x):\n"
        "    if x == 'hang':\n"
        "        while True:\n"
        "            pass\n"
        "    return x\n"
    )
    fill = _write_fill(tmp_path, fill_src)
    config = _write_config(tmp_path, [
        {"case": "c-hang", "input": "hang", "expect": "hang"},
        {"case": "c-ok", "input": "fine", "expect": "fine"},
    ])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out), "--case-timeout", "1"],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"}, timeout=30)

    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    by_case = {c["case"]: c for c in evidence["cases"]}
    assert by_case["c-hang"]["status"] == "TIMEOUT"
    assert by_case["c-ok"]["status"] == "PASS"


def test_crash_case_recorded(tmp_path):
    fill = _write_fill(tmp_path, "def run(x):\n    raise ValueError('boom-crash-test')\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    case = evidence["cases"][0]
    assert case["status"] == "CRASH"
    assert "boom-crash-test" in case["stderr"]
    assert "Traceback" in case["stderr"]


def test_empty_emit_is_crash(tmp_path):
    fill = _write_fill(tmp_path, "import sys\n\ndef run(x):\n    sys.exit(0)\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    case = evidence["cases"][0]
    assert case["status"] == "CRASH"
    assert case["got"] is None


def test_lying_eq_cannot_forge_pass(tmp_path):
    fill_src = (
        "class LyingStr(str):\n"
        "    def __eq__(self, other):\n"
        "        return True\n"
        "    def __ne__(self, other):\n"
        "        return False\n"
        "    def __repr__(self):\n"
        "        return 'totally-fine-repr'\n"
        "\n"
        "def run(x):\n"
        "    return LyingStr('actually-wrong-value')\n"
    )
    fill = _write_fill(tmp_path, fill_src)
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "correct-value"}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    case = evidence["cases"][0]
    assert case["status"] == "FAIL"
    assert case["got"] == "actually-wrong-value"
    assert case["got"] != "totally-fine-repr"


def test_fill_cannot_read_expect(tmp_path):
    fill_src = (
        "import json\n\n"
        "json.dumps = lambda *a, **k: '\"HIJACKED\"'\n\n"
        "def run(x):\n"
        "    leaked = None\n"
        "    try:\n"
        "        with open('eval_config.json', 'r', encoding='utf-8') as fh:\n"
        "            leaked = fh.read()\n"
        "    except OSError:\n"
        "        leaked = None\n"
        "    return {'leaked': leaked, 'marker': 'real-value'}\n"
    )
    fill = _write_fill(tmp_path, fill_src)
    config = _write_config(tmp_path, [
        {"case": "c1", "input": "x", "expect": {"leaked": None, "marker": "real-value"}},
    ])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    case = evidence["cases"][0]
    assert case["status"] == "PASS"
    assert case["got"]["leaked"] is None


def test_emit_channel_is_file_not_fd(tmp_path):
    fill_src = (
        "import json\n\n"
        "def run(x):\n"
        "    print(json.dumps({'got': 'WRONG-VIA-STDOUT'}))\n"
        "    return 'correct-value'\n"
    )
    fill = _write_fill(tmp_path, fill_src)
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "correct-value"}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    case = evidence["cases"][0]
    assert case["status"] == "PASS"
    assert case["got"] == "correct-value"


def test_sandbox_cleanup(tmp_path):
    mod = _load()
    created = []
    original_mkdtemp = mod.tempfile.mkdtemp

    def _spy(*args, **kwargs):
        p = original_mkdtemp(*args, **kwargs)
        created.append(p)
        return p

    import unittest.mock as mock
    fill = _write_fill(tmp_path, "def run(x):\n    return x\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    with mock.patch.object(mod.tempfile, "mkdtemp", side_effect=_spy):
        with mock.patch.dict(os.environ, {"HARNESS_R9_CONTAINMENT": "fallback"}):
            rc = mod.main(["--fill", str(fill), "--entry", "run", "--config", str(config),
                           "--evidence-out", str(evidence_out)])

    assert rc == 0
    main_dirs = [p for p in created if "probe" not in Path(p).name]
    assert main_dirs, "sandbox_run never created a sandbox dir"
    for p in main_dirs:
        assert not Path(p).exists()


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed on this host")
def test_bwrap_containment_when_present(tmp_path):
    fill_src = (
        "def run(payload):\n"
        "    path_parts = ['/tmp', 'eval-r9-host-marker-should-not-exist.txt']\n"
        "    target = path_parts[0] + '/' + path_parts[1]\n"
        "    with open(target, 'w') as fh:\n"
        "        fh.write('leaked')\n"
        "    return payload\n"
    )
    fill = _write_fill(tmp_path, fill_src)
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"
    host_marker = Path("/tmp/eval-r9-host-marker-should-not-exist.txt")
    if host_marker.exists():
        host_marker.unlink()

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "bwrap"})

    try:
        assert result.returncode == 0, result.stdout + result.stderr
        evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
        assert evidence["containment"] == "bwrap"
        # The write itself "succeeds" from the fill's point of view (bwrap
        # gives the sandbox its own private /tmp), but it never reaches the
        # real host path -- this is exactly the case a static denylist
        # cannot catch (computed path, not a literal), so it demonstrates
        # why layer 0 carries the real load.
        assert not host_marker.exists()
    finally:
        if host_marker.exists():
            host_marker.unlink()


def test_fallback_warns_when_absent(tmp_path):
    fill = _write_fill(tmp_path, "def run(x):\n    return x\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "best-effort" in result.stdout.lower()
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert evidence["containment"] == "python-filter-fallback"


def test_containment_error_exit_4(tmp_path, monkeypatch):
    mod = _load()
    fill = _write_fill(tmp_path, "def run(x):\n    return x\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    monkeypatch.setattr(mod, "_bwrap_preflight", lambda python_exe, bwrap_path: (False, "mock: forced failure", False))
    monkeypatch.setenv("HARNESS_R9_CONTAINMENT", "bwrap")

    rc = mod.main(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)])

    assert rc == 4
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert evidence["containment"] == "bwrap_failed"
    assert evidence["cases"] == []


def test_numeric_coercion_rules():
    mod = _load()
    eq = mod.canonical_equal
    assert eq(100, 100.0) is True
    assert eq(100, "100") is False
    assert eq(-0.0, 0.0) is True
    assert eq(5, float("nan")) is False
    assert eq(True, 1) is False
    assert eq(1, True) is False
    assert eq({"a": 1}, {"a": 1}) is True
    assert eq({"a": 1}, {"a": 1, "b": 2}) is False
    assert eq([1, 2], [1, 2]) is True
    assert eq([1, 2], [1, 2, 3]) is False
    assert eq(None, None) is True
    assert eq(None, 0) is False


def test_nan_in_expect_rejected_at_load(tmp_path):
    mod = _load()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"case_matrix": [
        {"case": "c1", "input": "x", "expect": float("nan")},
    ]}), encoding="utf-8")

    with pytest.raises(mod._InputError):
        mod._load_case_matrix(config)


def test_evidence_stable_shape(tmp_path):
    fill = _write_fill(tmp_path, _ROBUST_KV_FILL)
    config = _write_config(tmp_path, [{"case": "c1", "input": "name: x", "expect": {"name": "x"}}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run_pipeline", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert set(evidence.keys()) == {
        "schema_version", "fill", "entry", "card_hash", "containment",
        "denylist", "cases", "edge_cases", "summary", "meta", "actor", "ts",
    }
    for case in evidence["cases"] + evidence["edge_cases"]:
        assert set(case.keys()) == {"case", "input_digest", "status", "expect", "got", "stderr"}
    assert set(evidence["denylist"].keys()) == {"clean", "findings"}
    assert set(evidence["summary"].keys()) == {"total", "pass", "fail"}
    assert set(evidence["meta"].keys()) == {"env_keys", "bwrap_share_net"}


# --------------------------------------------------------------------------
# Finding #1 -- canonical_equal must not float-coerce two distinct large ints
# --------------------------------------------------------------------------

def test_numeric_large_int_not_coerced():
    mod = _load()
    eq = mod.canonical_equal
    assert eq(9007199254740993, 9007199254740992) is False
    assert eq(9007199254740993, 9007199254740993) is True
    # Mixed int/float past 2**53: float-coercing BOTH sides collapses the
    # distinct int onto the float and returns a false match. CPython's native
    # int/float compare is exact, so these must stay distinct.
    assert eq(9007199254740993, 9007199254740992.0) is False
    assert eq(9007199254740992.0, 9007199254740993) is False
    assert eq(9007199254740992, 9007199254740992.0) is True


def test_large_int_case_fails_not_passes(tmp_path):
    fill = _write_fill(tmp_path, "def run(x):\n    return 9007199254740992\n")
    config = _write_config(tmp_path, [
        {"case": "c1", "input": "x", "expect": 9007199254740993},
    ])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    case = evidence["cases"][0]
    assert case["status"] == "FAIL"


# --------------------------------------------------------------------------
# Extra-file scan lane must key off the DESTINATION name (what it runs as),
# not the source suffix -- else a .ts-suffixed Python payload skips the AST lane
# --------------------------------------------------------------------------

def test_extra_file_scanned_by_destination_not_source_suffix(tmp_path):
    fill = _write_fill(tmp_path, "import helper\ndef run(x):\n    return helper.go()\n")
    # SOURCE suffix .ts (routes to the weak js regex lane) but the file is
    # ordinary Python and lands in the sandbox as helper.py -- imported and
    # executed as Python. The AST lane must still refuse the os.system call.
    payload = tmp_path / "payload.ts"
    payload.write_text("import os\ndef go():\n    os.system('id')\n    return 'x'\n", encoding="utf-8")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "ev.json"

    result = _run(["--fill", str(fill), "--entry", "run",
                   "--extra-file", "helper.py=%s" % payload,
                   "--config", str(config), "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 3, result.stdout + result.stderr  # EXIT_DENYLIST_REFUSE
    assert "exec" in result.stdout


# --------------------------------------------------------------------------
# TOCTOU -- the sandbox must execute the SCANNED bytes, not re-read from disk
# --------------------------------------------------------------------------

def test_sandbox_executes_scanned_bytes_not_disk_reread(tmp_path, monkeypatch):
    mod = _load()
    fill = _write_fill(tmp_path, "def run(x):\n    return 'CLEAN'\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "CLEAN"}])
    evidence_out = tmp_path / "ev.json"

    # Simulate an attacker (or a repointed symlink / concurrent writer) that
    # rewrites the fill on disk in the window between the denylist read+scan and
    # the sandbox materialisation. The scanned bytes ('CLEAN') must be what runs.
    real_mksandbox = mod._make_sandbox_dir

    def _swap_in_window(*a, **k):
        d = real_mksandbox(*a, **k)
        fill.write_text("def run(x):\n    return 'SWAPPED'\n", encoding="utf-8")
        return d

    monkeypatch.setattr(mod, "_make_sandbox_dir", _swap_in_window)
    monkeypatch.setenv("HARNESS_R9_CONTAINMENT", "fallback")

    rc = mod.main(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)])

    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert evidence["cases"][0]["got"] == "CLEAN", "executed the swapped disk bytes, not the scanned bytes"
    assert rc == 0


# --------------------------------------------------------------------------
# R9 entry arity: the driver calls entry(case_input) with ONE arg, so a 2-arg
# score_dimension must be gated via a single-arg wrapper (documented contract)
# --------------------------------------------------------------------------

def test_r9_two_arg_score_dimension_needs_single_arg_wrapper(tmp_path):
    fill = _write_fill(tmp_path,
        "def score_dimension(dimension_name, results):\n"
        "    return float(len(results)) * 10.0\n"
        "def score_probe(case_input):\n"
        "    return score_dimension('accuracy', [case_input])\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": 10.0}])

    # the documented single-arg wrapper entry runs clean through R9
    good = _run(["--fill", str(fill), "--entry", "score_probe", "--config", str(config),
                 "--evidence-out", str(tmp_path / "ev.json")],
                env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})
    assert good.returncode == 0, good.stdout + good.stderr

    # the bare 2-arg entry is a per-case dead end (TypeError), never a clean PASS
    ev2 = tmp_path / "ev2.json"
    bad = _run(["--fill", str(fill), "--entry", "score_dimension", "--config", str(config),
                "--evidence-out", str(ev2)],
               env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})
    assert bad.returncode != 0
    assert json.loads(ev2.read_text(encoding="utf-8"))["cases"][0]["status"] != "PASS"


# --------------------------------------------------------------------------
# Finding #2/#3 -- bwrap preflight vs real-run PATH mismatch
# --------------------------------------------------------------------------

def test_bwrap_resolved_by_absolute_path_not_PATH(tmp_path, monkeypatch):
    if shutil.which("bwrap") is None:
        pytest.skip("bwrap not installed on this host")
    mod = _load()

    # Simulate a host where the scrubbed per-case env's PATH does not
    # contain wherever bwrap actually lives (e.g. /usr/local/bin) -- the
    # real preflight `subprocess.run` call inherits the full host PATH and
    # finds bwrap fine regardless; only the per-case scrubbed env is
    # stripped here, host-independent of where bwrap really sits.
    real_build_env = mod._build_env

    def _stripped_env(sandbox_dir, nonce=None):
        env = dict(real_build_env(sandbox_dir, nonce))
        env["PATH"] = str(tmp_path / "nowhere")  # bwrap is NOT resolvable here
        return env

    monkeypatch.setattr(mod, "_build_env", _stripped_env)
    monkeypatch.setenv("HARNESS_R9_CONTAINMENT", "bwrap")

    fill = _write_fill(tmp_path, "def run(x):\n    return x\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    rc = mod.main(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)])

    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert rc == 0, evidence
    assert evidence["containment"] == "bwrap"
    for case in evidence["cases"]:
        assert case["status"] != "CRASH", case


def test_bwrap_launch_failure_is_containment_error_not_crash(tmp_path, monkeypatch):
    mod = _load()
    fill = _write_fill(tmp_path, "def run(x):\n    return x\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    monkeypatch.setattr(mod, "_resolve_bwrap_path", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(mod, "_bwrap_preflight", lambda python_exe, bwrap_path: (True, "", False))
    monkeypatch.setattr(mod, "_probe_env", lambda *a, **k: ["PATH"])

    def _boom(*args, **kwargs):
        raise OSError("simulated bwrap launch failure mid-run")

    monkeypatch.setattr(mod.subprocess, "Popen", _boom)
    monkeypatch.setenv("HARNESS_R9_CONTAINMENT", "bwrap")

    rc = mod.main(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)])

    assert rc == 4, "a mid-run bwrap launch failure must be exit 4 (infra), not a CRASH case"
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert evidence["containment"] == "bwrap_failed"
    for c in evidence["cases"]:
        assert c["status"] != "CRASH"


# --------------------------------------------------------------------------
# Finding #4 -- an edge case cannot be forged by a fill-written emit file
# --------------------------------------------------------------------------

def test_edge_case_cannot_be_forged_by_written_emit(tmp_path):
    fill_src = (
        "import os\n\n"
        "def run(x):\n"
        "    token = None\n"
        "    for name in os.listdir('.'):\n"
        "        if name.startswith('case-input-') and name.endswith('.json'):\n"
        "            token = name[len('case-input-'):-len('.json')]\n"
        "            break\n"
        "    if token:\n"
        "        with open('emit-%s.json' % token, 'w') as fh:\n"
        "            fh.write('{\"got\": \"forged-pass\", \"nonce\": \"wrong-nonce\"}')\n"
        "    raise ValueError('intentional crash to test forged-emit closure')\n"
    )
    fill = _write_fill(tmp_path, fill_src)
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out)],
                  env_overrides={"HARNESS_R9_CONTAINMENT": "fallback"})

    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    for case in evidence["cases"] + evidence["edge_cases"]:
        assert case["status"] == "CRASH", case


# --------------------------------------------------------------------------
# Finding #5 -- --extra-file must be denylist-scanned and its name sanitized
# --------------------------------------------------------------------------

def test_extra_file_is_denylist_scanned(tmp_path):
    fill = _write_fill(tmp_path, "def run(x):\n    return x\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    extra = tmp_path / "helper.py"
    extra.write_text("import socket\n\ndef helper():\n    return socket.socket()\n",
                      encoding="utf-8")
    evidence_out = tmp_path / "evidence.json"

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out),
                   "--extra-file", "helper.py=%s" % extra])

    assert result.returncode == 3, result.stdout + result.stderr
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert evidence["denylist"]["clean"] is False
    assert any(f["code"] == "network" for f in evidence["denylist"]["findings"])


def test_extra_file_name_rejects_traversal(tmp_path):
    fill = _write_fill(tmp_path, "def run(x):\n    return x\n")
    config = _write_config(tmp_path, [{"case": "c1", "input": "x", "expect": "x"}])
    extra = tmp_path / "evil.py"
    extra.write_text("EVIL = 1\n", encoding="utf-8")
    evidence_out = tmp_path / "evidence.json"
    outside_marker = tmp_path.parent / "escaped-evil.py"
    if outside_marker.exists():
        outside_marker.unlink()

    result = _run(["--fill", str(fill), "--entry", "run", "--config", str(config),
                   "--evidence-out", str(evidence_out),
                   "--extra-file", "../escaped-evil.py=%s" % extra])

    assert result.returncode == 2, result.stdout + result.stderr
    assert not outside_marker.exists()


def test_bwrap_safe_base_avoids_dev(monkeypatch):
    """bwrap's --dev remounts /dev and shadows a sandbox under /dev/shm; the
    safe-base picker must steer the sandbox off /dev when bwrap is in play."""
    mod = _load()
    # not bwrap -> the default temp base is always fine (None)
    assert mod._bwrap_safe_base(False) is None
    # bwrap + default temp under /dev/shm -> a base OUTSIDE /dev
    monkeypatch.setattr(mod.tempfile, "gettempdir", lambda: "/dev/shm")
    base = mod._bwrap_safe_base(True)
    assert base is not None and not os.path.realpath(base).startswith("/dev")
    # bwrap + already-safe default (/tmp) -> None (leave the default alone)
    monkeypatch.setattr(mod.tempfile, "gettempdir", lambda: "/tmp")
    assert mod._bwrap_safe_base(True) is None
