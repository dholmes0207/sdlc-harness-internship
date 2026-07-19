"""engine_skew_nudge — SessionStart courier health advisory.

Covers the three advisory triggers (unresolved / integrity / skew), the once-per-
session gate, the self-host silence, fail-open at subprocess level, AND a real
run through hook_dispatch.py — because the dispatcher calls the registered entry
DIRECTLY, so the once-per-session gate MUST live in core() (a known trap).
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS = _REPO_ROOT / "harness" / "hooks"
for _p in (str(_HOOKS), str(_REPO_ROOT / "harness" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine_skew_nudge as esn  # noqa: E402


def _mini_engine(path: Path, version: str) -> Path:
    root = path
    (root / "harness" / "hooks").mkdir(parents=True)
    payload = b"hello world"
    (root / "harness" / "x.txt").write_bytes(payload)
    manifest = {"files": {"harness/x.txt": hashlib.sha256(payload).hexdigest()}}
    (root / "harness" / "manifest.json").write_text(json.dumps(manifest))
    (root / "harness" / "release.json").write_text(
        json.dumps({"harness_version": version}))
    return root


def _make_cache(home: Path, version: str) -> None:
    d = (home / ".claude" / "plugins" / "cache" / "mkt" / "harness" / version
         / "engine" / "harness")
    d.mkdir(parents=True)
    (d / "release.json").write_text(json.dumps({"harness_version": version}))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    monkeypatch.delenv("HARNESS_BIN_ROOT", raising=False)
    return {"home": home, "tmp": tmp_path}


def test_self_host_silent(env, monkeypatch):
    monkeypatch.delenv("HARNESS_BIN_ROOT", raising=False)
    assert esn.core({"session_id": "s1"}) is None


def test_unresolved_root_advisory(env, monkeypatch):
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(env["tmp"] / "gone"))
    out = esn.core({"session_id": "s1"})
    assert out and "unresolved" in out.lower()


def test_integrity_drift_advisory(env, monkeypatch, tmp_path):
    root = _mini_engine(tmp_path / "eng", "1.0.0")
    # corrupt the tracked file — same version, drifted hash (F8)
    (root / "harness" / "x.txt").write_bytes(b"TAMPERED")
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(root))
    out = esn.core({"session_id": "s1"})
    assert out and "integrity" in out.lower()


def test_skew_advisory(env, monkeypatch, tmp_path):
    root = _mini_engine(tmp_path / "eng", "1.0.0")
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(root))
    _make_cache(env["home"], "2.0.0")  # newer cache
    out = esn.core({"session_id": "s1"})
    assert out and "2.0.0" in out and "upgrade" in out.lower()


def test_equal_version_silent(env, monkeypatch, tmp_path):
    root = _mini_engine(tmp_path / "eng", "1.0.0")
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(root))
    _make_cache(env["home"], "1.0.0")  # same version, clean integrity
    assert esn.core({"session_id": "s1"}) is None


def test_once_per_session(env, monkeypatch, tmp_path):
    root = _mini_engine(tmp_path / "eng", "1.0.0")
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(root))
    _make_cache(env["home"], "2.0.0")
    first = esn.core({"session_id": "sX"})
    second = esn.core({"session_id": "sX"})
    assert first is not None
    assert second is None, "advisory fired twice in one session"


def test_fail_open_subprocess(env):
    r = subprocess.run(
        [sys.executable, str(_HOOKS / "engine_skew_nudge.py")],
        input="garbage", capture_output=True, text=True)
    assert r.returncode == 0
    json.loads(r.stdout or '{"continue": true}')


def test_via_dispatcher_subprocess(env, tmp_path):
    """Drive the REAL dispatcher: the skew advisory must surface AND the once-gate
    (living in core, not run/main) must hold across two dispatcher invocations."""
    root = _mini_engine(tmp_path / "eng", "1.0.0")
    _make_cache(env["home"], "2.0.0")
    penv = dict(os.environ)
    penv["HARNESS_BIN_ROOT"] = str(root)
    penv["HOME"] = str(env["home"])
    penv["TMPDIR"] = str(env["tmp"] / "tmp")
    # read the SHIPPED hook config (engine_skew_nudge ON), not a dev override.
    penv["HARNESS_HOOK_CONFIG"] = str(_REPO_ROOT / "harness" / "data" / "harness-hooks.yaml")
    stdin = json.dumps({"session_id": "disp1", "source": "startup"})

    def _run():
        return subprocess.run(
            [sys.executable, str(_HOOKS / "hook_dispatch.py"), "SessionStart"],
            input=stdin, capture_output=True, text=True, env=penv)

    r1 = _run()
    assert r1.returncode == 0, r1.stderr
    blob1 = r1.stdout
    assert "upgrade" in blob1 and "2.0.0" in blob1, \
        "skew advisory did not surface through the dispatcher: %r" % blob1[:300]

    r2 = _run()
    assert r2.returncode == 0
    assert "2.0.0" not in r2.stdout, \
        "once-per-session gate leaked past the dispatcher (gate not in core)"


def test_semver_key_normalizes_length_and_prerelease():
    assert esn._semver_key("1.2") == esn._semver_key("1.2.0")
    # a pre-release sorts BELOW its final, so an rc->final cache upgrade still nudges
    assert esn._semver_key("1.2.0-rc1") < esn._semver_key("1.2.0")
    assert esn._semver_key("2.0.0") > esn._semver_key("1.9.9")
    assert esn._semver_key("4.0.10") > esn._semver_key("4.0.9")


def test_healthy_engine_marks_session_after_check(env, monkeypatch, tmp_path):
    root = _mini_engine(tmp_path / "eng", "1.0.0")
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(root))
    _make_cache(env["home"], "1.0.0")  # equal version → healthy, no advisory
    assert esn.core({"session_id": "hs"}) is None
    # the (expensive) check must be marked done so it does NOT re-run this session
    assert esn._flag_path("hs").exists()


def test_anonymous_session_not_gated(env, monkeypatch, tmp_path):
    root = _mini_engine(tmp_path / "eng", "1.0.0")
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(root))
    _make_cache(env["home"], "2.0.0")
    # empty session_id must NOT write/read the shared "_" flag (would be once-ever)
    assert esn.core({"session_id": ""}) is not None
    assert esn.core({"session_id": ""}) is not None


def test_advisory_reaches_human_under_shipped_context_surface(env, tmp_path):
    """Round-4 regression: the SHIPPED context-surface.yaml has
    session_start.system_message=false (the additionalContext->systemMessage mirror is
    OFF), so the human channel MUST come from this hook's own systemMessage queue —
    or a courier operator with a broken engine sees nothing."""
    root = _mini_engine(tmp_path / "eng", "1.0.0")
    _make_cache(env["home"], "2.0.0")
    penv = dict(os.environ)
    penv["HARNESS_BIN_ROOT"] = str(root)
    penv["HOME"] = str(env["home"])
    penv["TMPDIR"] = str(env["tmp"] / "tmp")
    penv["HARNESS_HOOK_CONFIG"] = str(_REPO_ROOT / "harness" / "data" / "harness-hooks.yaml")
    penv["HARNESS_CONTEXT_SURFACE"] = str(_REPO_ROOT / "harness" / "data" / "context-surface.yaml")
    r = subprocess.run(
        [sys.executable, str(_HOOKS / "hook_dispatch.py"), "SessionStart"],
        input=json.dumps({"session_id": "ship-vis", "source": "startup"}),
        capture_output=True, text=True, env=penv)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "upgrade" in out.get("systemMessage", ""), \
        "human systemMessage missing under shipped context-surface (mirror off): %r" % out


def _place_home_version(eh, version, current=False):
    vd = eh / version
    (vd / "harness" / "hooks").mkdir(parents=True)
    p = b"engine bytes"
    (vd / "harness" / "x.txt").write_bytes(p)
    (vd / "harness" / "manifest.json").write_text(json.dumps(
        {"files": {"harness/x.txt": hashlib.sha256(p).hexdigest()}}))
    (vd / "harness" / "release.json").write_text(json.dumps({"harness_version": version}))
    if current:
        (eh / "current").symlink_to(version)
    return vd


def test_skew_keys_off_max_installed_not_current(env, monkeypatch):
    """Round-10: on a pinned-newer home the skew nudge must key off max-installed
    (like upgrade/doctor), not the resolved current version — else it advises an
    upgrade that upgrade() refuses."""
    eh = env["home"] / ".local" / "share" / "harness"
    eh.mkdir(parents=True)
    _place_home_version(eh, "3.0.0", current=True)
    _place_home_version(eh, "5.0.0")  # pinned-newer; current stays 3.0.0
    _make_cache(env["home"], "4.0.0")  # cache between the two
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(eh / "current"))
    out = esn.core({"session_id": "floor-skew"})
    assert out is None or "upgrade" not in out, \
        "skew nudge advised an upgrade that upgrade() would refuse: %r" % out


def test_installed_floor_matches_lifecycle_floor(env):
    import sys as _s
    for _p in (str(_REPO_ROOT / "harness" / "scripts"),
               str(_REPO_ROOT / "harness" / "install")):
        if _p not in _s.path:
            _s.path.insert(0, _p)
    import harness_lifecycle as L
    eh = env["home"] / ".local" / "share" / "harness"
    eh.mkdir(parents=True)
    for v in ("1.2.0", "5.0.0", "0.9.0", "5.0.0-rc1"):
        _place_home_version(eh, v)
    assert esn._installed_floor() == L._home_floor_version(eh)
