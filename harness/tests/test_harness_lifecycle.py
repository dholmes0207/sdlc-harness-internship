"""harness_lifecycle.py — the courier's on-PATH lifecycle tool (setup / upgrade /
version / path / doctor). Every test runs in a fake HOME + a tmp target repo, so
nothing touches the real machine; a guard asserts the real ~/.claude/settings.json
is never written.

The engine used for setup is a REAL courier engine built once from this repo's
pack tarball (module-scoped) — setup's verify-before-repoint step (red-team F5)
only passes against a genuinely valid engine, so a mini stub would not exercise it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "harness" / "scripts"),
           str(_REPO_ROOT / "harness" / "install"),
           str(_REPO_ROOT / "release")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_manifest  # noqa: E402
import pack  # noqa: E402
import courier_tree  # noqa: E402
import harness_lifecycle  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, check=True)


def _tracked_harness_rels():
    out = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "-c", "core.quotepath=false",
         "ls-files", "-z", "--", "harness/"],
        capture_output=True, text=True, check=True)
    return [l for l in out.stdout.split("\0") if l.strip()]


@pytest.fixture(scope="module")
def real_courier(tmp_path_factory):
    """Build the courier tree once from an ISOLATED source whose manifest is
    rebuilt in place — so the fixture is immune to a stale live manifest during
    development (mirrors release/tests/test_install_seam.py). Returns the
    <courier>/engine dir (contains harness/) — the `source_engine` setup copies."""
    d = tmp_path_factory.mktemp("courier")
    src = d / "source"
    for rel in _tracked_harness_rels():
        s = _REPO_ROOT / rel
        if not s.is_file():
            continue
        dst = src / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(s.read_bytes())
    # release.json is git-tracked under harness/ and carried by the copy above.
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@t")
    _git(src, "config", "user.name", "t")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "seed")
    build_manifest.main(["--root", str(src)])

    tarball = d / "bundle.tar.gz"
    files = pack.manifest_files(src)
    pack.build_tarball(src, files, tarball)
    tree = d / "tree"
    courier_tree.build_courier_tree(tarball, tree, repo_root=d / "outside")
    return tree / "engine"


@pytest.fixture()
def target_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("HARNESS_BIN_ROOT", raising=False)
    monkeypatch.delenv("HARNESS_DATA_ROOT", raising=False)
    return home


def _version_of(engine):
    return json.loads((engine / "harness" / "release.json").read_text())["harness_version"]


def test_setup_full_flow(real_courier, target_repo, fake_home):
    res = harness_lifecycle.setup(target_repo, source_engine=real_courier)
    v = res["version"]
    eh = fake_home / ".local" / "share" / "harness"

    # engine lives at engine_home/<version>/harness
    assert (eh / v / "harness" / "scripts" / "harness_paths.py").is_file()
    # current -> version
    cur = eh / "current"
    assert cur.is_symlink()
    assert cur.resolve() == (eh / v).resolve()

    # per-project env in the TARGET repo's settings.local.json (F3)
    sl = json.loads((target_repo / ".claude" / "settings.local.json").read_text())
    assert sl["env"]["HARNESS_BIN_ROOT"] == str(eh / "current")
    # per-project DATA root wired too — belt+suspenders, mirrors --global: a
    # Bash-tool call (no CLAUDE_PROJECT_DIR in its env) resolves state from the
    # injected settings env even when the SessionStart seed has not fired.
    assert sl["env"]["HARNESS_DATA_ROOT"] == str(target_repo / ".harness")

    # global ~/.claude/settings.json is NEVER written (F3)
    assert not (fake_home / ".claude" / "settings.json").exists()

    # the 4 wiring pieces: env (above), hooks, marketplace, skeleton
    assert sl.get("hooks"), "hooks not wired"
    mk = sl.get("extraKnownMarketplaces", {})
    assert "hs-local" in mk
    assert mk["hs-local"]["source"]["path"] == str(eh / "current" / "harness" / "plugins")
    assert any(k.startswith("hs@") for k in sl.get("enabledPlugins", {}))
    assert (target_repo / ".harness").is_dir()

    # the installed engine verifies clean under --strict (F5 guarantee)
    r = subprocess.run(
        [sys.executable, str(eh / v / "harness" / "scripts" / "verify_install.py"),
         "--root", str(eh / v), "--strict"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_setup_verify_fail_no_repoint(real_courier, target_repo, fake_home, tmp_path):
    # Corrupt a copy of the engine so verify fails: delete a manifest-listed file.
    import shutil
    broken = tmp_path / "broken-engine"
    shutil.copytree(real_courier, broken)
    victim = broken / "harness" / "scripts" / "harness_paths.py"
    victim.unlink()  # now on disk it is missing but manifest lists it -> verify FAIL

    eh = fake_home / ".local" / "share" / "harness"
    with pytest.raises(harness_lifecycle.LifecycleError):
        harness_lifecycle.setup(target_repo, source_engine=broken)

    # machine untouched: no current pointer, no env written, no tmp residue
    assert not (eh / "current").exists()
    assert not (target_repo / ".claude" / "settings.local.json").exists()
    leftovers = list(eh.glob("*.tmp*")) + list(eh.glob(".*.tmp*")) if eh.exists() else []
    assert not leftovers, "verify-fail left a tmp staging dir behind"


def test_setup_refuses_existing_embedded_harness_tree(real_courier, target_repo, fake_home):
    # A courier setup serves the shared binary via env-resolve — it must refuse to
    # overlay a project that already carries its own per-project harness/ tree
    # (split-brain: two trees, ambiguous which the guards resolve), mirroring
    # install.py --global's refuse. Refuse EARLY: no engine install, no wiring.
    (target_repo / "harness").mkdir()
    eh = fake_home / ".local" / "share" / "harness"
    with pytest.raises(harness_lifecycle.LifecycleError) as ei:
        harness_lifecycle.setup(target_repo, source_engine=real_courier)
    assert "per-project harness" in str(ei.value)
    # machine untouched: refuse fired before any install/wiring
    assert not (eh / "current").exists()
    assert not (target_repo / ".claude" / "settings.local.json").exists()


def test_setup_self_host_not_refused(real_courier, fake_home, tmp_path):
    # Self-host / dogfood (target IS the source engine) is EXEMPT: its harness/ tree
    # is the source, not a stale overlay. A copy stands in for the source==target
    # engine so the module-scoped real_courier is not mutated.
    import shutil
    self_host = tmp_path / "self-host-engine"
    shutil.copytree(real_courier, self_host)
    res = harness_lifecycle.setup(self_host, source_engine=self_host)
    assert res["version"]


def test_setup_idempotent(real_courier, target_repo, fake_home):
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    sl1 = (target_repo / ".claude" / "settings.local.json").read_text()
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    sl2 = (target_repo / ".claude" / "settings.local.json").read_text()
    assert sl1 == sl2, "second setup changed the wiring — not idempotent"


def test_setup_pin_wires_version_dir(real_courier, target_repo, fake_home):
    res = harness_lifecycle.setup(target_repo, source_engine=real_courier, pin=True)
    v = res["version"]
    eh = fake_home / ".local" / "share" / "harness"
    sl = json.loads((target_repo / ".claude" / "settings.local.json").read_text())
    # pinned: env points at the version dir, not the moving 'current'
    assert sl["env"]["HARNESS_BIN_ROOT"] == str(eh / v)


def test_setup_preserves_foreign_settings_keys(real_courier, target_repo, fake_home):
    claude = target_repo / ".claude"
    claude.mkdir()
    (claude / "settings.local.json").write_text(json.dumps(
        {"env": {"MY_OWN": "1"}, "permissions": {"allow": ["x"]}}))
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    sl = json.loads((claude / "settings.local.json").read_text())
    assert sl["env"]["MY_OWN"] == "1", "clobbered a foreign env key"
    assert sl["permissions"]["allow"] == ["x"], "clobbered a foreign top-level key"


def test_upgrade_side_by_side_no_rewire(real_courier, target_repo, fake_home, tmp_path):
    import shutil
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    v1 = _version_of(real_courier)
    eh = fake_home / ".local" / "share" / "harness"
    sl_before = (target_repo / ".claude" / "settings.local.json").read_text()

    # a "new version" engine: same tree, bumped release.json version
    v2 = "9.9.9-next"
    new_engine = tmp_path / "engine2"
    shutil.copytree(real_courier, new_engine)
    rjp = new_engine / "harness" / "release.json"
    rj = json.loads(rjp.read_text())
    rj["harness_version"] = v2
    rjp.write_text(json.dumps(rj, indent=2) + "\n")

    harness_lifecycle.upgrade(source_engine=new_engine)

    # both versions on disk side-by-side; current repointed to the new one
    assert (eh / v1 / "harness").is_dir()
    assert (eh / v2 / "harness").is_dir()
    assert (eh / "current").resolve() == (eh / v2).resolve()
    # upgrade never touches the target repo wiring
    assert (target_repo / ".claude" / "settings.local.json").read_text() == sl_before


def test_doctor_integrity_fail_on_corrupt_home(real_courier, target_repo, fake_home):
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    v = _version_of(real_courier)
    eh = fake_home / ".local" / "share" / "harness"
    # flip a byte in an installed script — same version, corrupt integrity
    victim = eh / v / "harness" / "scripts" / "harness_paths.py"
    victim.write_text(victim.read_text() + "\n# tampered\n")

    rc, report = harness_lifecycle.doctor(source_engine=real_courier, capture=True)
    assert "integrity" in report.lower() or "drift" in report.lower()
    assert "harness_paths.py" in report, "doctor did not name the drifted file (F8)"


def test_doctor_skew_advisory_not_blocking(real_courier, target_repo, fake_home, tmp_path):
    import shutil
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    # cache engine with a DIFFERENT version than the home
    newer = tmp_path / "cache-newer"
    shutil.copytree(real_courier, newer)
    rjp = newer / "harness" / "release.json"
    rj = json.loads(rjp.read_text()); rj["harness_version"] = "9.9.9-cache"
    rjp.write_text(json.dumps(rj, indent=2) + "\n")

    rc, report = harness_lifecycle.doctor(source_engine=newer, capture=True)
    assert rc == 0, "skew must be advisory, not a hard block (DEC handshake)"
    assert "9.9.9-cache" in report and "skew" in report.lower()


def test_path_verb(real_courier, target_repo, fake_home):
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    eh = fake_home / ".local" / "share" / "harness"
    root = harness_lifecycle.path_of()
    assert Path(root).resolve() == (eh / "current").resolve()
    sub = harness_lifecycle.path_of("harness/rules/output-rendering.md")
    assert sub.endswith("harness/rules/output-rendering.md")


def test_version_verb_reports_source(real_courier, target_repo, fake_home):
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    info = harness_lifecycle.version_info(source_engine=real_courier)
    assert info["home_version"] == _version_of(real_courier)
    assert "running_source" in info


def test_reinstall_after_harden_bin_does_not_crash(real_courier, target_repo, fake_home):
    """Regression: --harden-bin strips write bits from the version dir; a re-setup
    of the same version must force-remove it, not raise a raw PermissionError."""
    harness_lifecycle.setup(target_repo, source_engine=real_courier, harden_bin=True)
    # re-setup the SAME version → _install_version rmtrees the hardened version_dir
    res = harness_lifecycle.setup(target_repo, source_engine=real_courier, harden_bin=True)
    assert res["version"]
    eh = fake_home / ".local" / "share" / "harness"
    assert (eh / res["version"] / "harness" / "scripts").is_dir()


def test_copytree_oserror_wrapped_as_lifecycle_error(real_courier, target_repo,
                                                     fake_home, monkeypatch):
    """A shutil.Error / OSError while copying the source engine (unreadable cache
    file, NFS hiccup) is an OSError, not a LifecycleError — setup must wrap it in an
    actionable LifecycleError, not surface a raw traceback, and leave no tmp residue."""
    import shutil
    def _boom(*a, **k):
        raise OSError("simulated unreadable cache file")
    monkeypatch.setattr(harness_lifecycle.shutil, "copytree", _boom)
    eh = fake_home / ".local" / "share" / "harness"
    with pytest.raises(harness_lifecycle.LifecycleError) as ei:
        harness_lifecycle.setup(target_repo, source_engine=real_courier)
    assert "could not copy" in str(ei.value).lower()
    leftovers = list(eh.glob("*.tmp*")) + list(eh.glob(".*.tmp*")) if eh.exists() else []
    assert not leftovers, "a failed copy left a tmp staging dir behind"


def test_read_version_rejects_malformed_release_json(tmp_path):
    """A truncated/corrupt release.json (invalid-JSON) or a wrong-shape one (a JSON
    list) must raise the actionable LifecycleError from _read_version — never a raw
    JSONDecodeError/AttributeError that setup/upgrade would leak as a traceback."""
    eng = tmp_path / "eng"
    (eng / "harness").mkdir(parents=True)
    rj = eng / "harness" / "release.json"
    for bad in ('{"harness_version": "1.0.0"', '["harness_version", "1.0.0"]'):
        rj.write_text(bad, encoding="utf-8")
        with pytest.raises(harness_lifecycle.LifecycleError):
            harness_lifecycle._read_version(eng)


def test_maybe_version_none_on_wrong_shape_release_json(tmp_path):
    """_maybe_version is best-effort (feeds version/doctor); a wrong-shape release.json
    must degrade to None, not raise AttributeError on .get()."""
    eng = tmp_path / "eng"
    (eng / "harness").mkdir(parents=True)
    (eng / "harness" / "release.json").write_text('["harness_version", "1.0.0"]',
                                                  encoding="utf-8")
    assert harness_lifecycle._maybe_version(eng) is None  # must not raise


def test_doctor_survives_symlink_loop_bin_root(fake_home, tmp_path, monkeypatch):
    """`harness doctor` is the tool you run WHEN the engine is broken — a symlink loop
    at HARNESS_BIN_ROOT must not crash it with a raw RuntimeError('Symlink loop').
    Report rc=1 with an actionable message, not a traceback."""
    loop = tmp_path / "loop"
    a, b = loop / "a", loop / "b"
    loop.mkdir()
    a.symlink_to(b)
    b.symlink_to(a)  # a -> b -> a
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(a))
    rc, report = harness_lifecycle.doctor(capture=True)  # must NOT raise
    assert rc == 1
    assert "unresolvable" in report.lower(), report


def test_repoint_current_is_atomic_symlink(real_courier, target_repo, fake_home):
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    eh = fake_home / ".local" / "share" / "harness"
    cur = eh / "current"
    assert cur.is_symlink()
    # no leftover temp pointer from the atomic os.replace
    assert not list(eh.glob(".current.tmp.*"))


def _older_cache(real_courier, tmp_path, version="0.0.1"):
    import shutil
    older = tmp_path / "older-cache"
    shutil.copytree(real_courier, older)
    rj = older / "harness" / "release.json"
    d = json.loads(rj.read_text()); d["harness_version"] = version
    rj.write_text(json.dumps(d) + "\n")
    return older


def test_upgrade_refuses_downgrade(real_courier, target_repo, fake_home, tmp_path):
    """Round-5: doctor advises `harness upgrade` on skew; upgrade must REFUSE an
    older cache so it never silently downgrades the live engine home."""
    harness_lifecycle.setup(target_repo, source_engine=real_courier)  # home = real version
    older = _older_cache(real_courier, tmp_path)
    with pytest.raises(harness_lifecycle.LifecycleError) as ei:
        harness_lifecycle.upgrade(source_engine=older)
    assert "downgrade" in str(ei.value).lower()


def test_doctor_does_not_advise_upgrade_on_older_cache(real_courier, target_repo, fake_home, tmp_path):
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    older = _older_cache(real_courier, tmp_path)
    rc, rep = harness_lifecycle.doctor(source_engine=older, capture=True)
    assert "will NOT downgrade" in rep or "newer than the plugin cache" in rep
    # must NOT tell the user to run upgrade (that would downgrade)
    assert "run `harness upgrade` to apply" not in rep


def test_semver_key_matches_engine_skew_nudge():
    import sys as _s
    _s.path.insert(0, str(_REPO_ROOT / "harness" / "hooks"))
    import engine_skew_nudge as esn
    for v in ["1.2", "1.2.0", "1.2.0-rc1", "2.0.0", "4.0.10", "0.0.1", "9.9.9-next",
              "4.0.1-rc2", "4.0.1-rc10", "1.2.0-alpha", "1.2.0-1"]:
        assert harness_lifecycle._semver_key(v) == esn._semver_key(v), v


def test_semver_key_orders_prereleases_not_collapse():
    # Round-23: pre-release identifiers must ORDER, not collapse to one key. Collapsing
    # rc1==rc2==rc10 silently defeats the forward-only downgrade guard — _repoint_current
    # would roll `current` from rc2 back to rc1 because rc1 is not strictly < rc2. A
    # pre-release sorts below its final; numeric identifiers sort below alpha (semver §11).
    k = harness_lifecycle._semver_key
    # distinct pre-releases must NOT collapse to one key (the actual bug)
    assert len({k("4.0.1-rc1"), k("4.0.1-rc2"), k("4.0.1-rc10")}) == 3
    # single alphanumeric identifiers order lexically (semver §11.4.2): rc1 < rc2
    assert k("4.0.1-rc1") < k("4.0.1-rc2")
    # dot-separated numeric identifiers order numerically (§11.4.1): rc.2 < rc.10
    assert k("4.0.1-rc.2") < k("4.0.1-rc.10")
    assert k("1.2.0-1") < k("1.2.0-alpha"), "numeric identifier must sort below alpha"
    assert k("1.2.0-rc1") < k("1.2.0"), "a pre-release must sort below its final"


def test_repoint_refuses_prerelease_downgrade(fake_home, tmp_path, monkeypatch):
    # Round-23: the forward-only guard must refuse rolling `current` from rc2 back to
    # rc1 — a real (non-advisory) downgrade of the shared pointer, not just a nudge.
    eh = tmp_path / "engine"
    for v in ("4.0.1-rc1", "4.0.1-rc2"):
        (eh / v / "harness").mkdir(parents=True)
        (eh / v / "harness" / "VERSION").write_text(v)
        # _installed_versions (the forward-only floor) counts a dir only if it carries
        # harness/release.json, so write one for each installed version.
        (eh / v / "harness" / "release.json").write_text('{"harness_version": "%s"}' % v)
    monkeypatch.setattr(harness_lifecycle.harness_paths, "engine_home", lambda: eh)
    harness_lifecycle._repoint_current(eh, "4.0.1-rc2")
    assert (eh / "current").resolve().name == "4.0.1-rc2"
    harness_lifecycle._repoint_current(eh, "4.0.1-rc1")  # forward-only must refuse
    assert (eh / "current").resolve().name == "4.0.1-rc2", "rc2 -> rc1 silent downgrade"


def test_same_version_reinstall_no_read_window(real_courier, target_repo, fake_home):
    """Round-5 F-1: a same-version reinstall must NOT destroy the version dir that
    `current` resolves to — a live multi-project session reading the engine during
    the window would hit FileNotFoundError storms. The skip-if-clean path avoids it."""
    import threading
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    eh = fake_home / ".local" / "share" / "harness"
    probe = eh / "current" / "harness" / "scripts" / "harness_paths.py"
    assert probe.exists()

    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                (eh / "current" / "harness" / "scripts" / "harness_paths.py").read_bytes()
            except FileNotFoundError:
                errors.append(1)
            except OSError:
                pass

    t = threading.Thread(target=reader)
    t.start()
    try:
        for _ in range(5):  # repeated same-version reinstalls widen any window
            harness_lifecycle.setup(target_repo, source_engine=real_courier)
    finally:
        stop.set()
        t.join(timeout=5)
    assert not errors, \
        "reads through `current` 404'd %d times during a same-version reinstall" % len(errors)


def test_concurrent_first_install_of_same_version_is_idempotent(real_courier, target_repo, fake_home, monkeypatch):
    """Round-22: two `setup` racing to FIRST-install the SAME new version both pass
    the missing-version check, then race the swap. The loser must be treated as an
    idempotent success (a racer already left a clean copy) — NOT surfaced as a
    misleading 'a hardened engine may need chmod' LifecycleError (nothing was
    hardened), and NOT allowed to destroy the winner's dir a live session may read."""
    import threading
    import time
    eh = fake_home / ".local" / "share" / "harness"
    version = harness_lifecycle._read_version(real_courier)
    version_dir = eh / version

    real_replace = os.replace
    barrier = threading.Barrier(2)
    order = []
    olock = threading.Lock()

    def gated_replace(src, dst):
        if str(dst) == str(version_dir):
            # both threads arrive here only AFTER passing `version_dir.exists()`==False
            # (no swap has completed yet), forcing the true TOCTOU window.
            barrier.wait(timeout=10)
            with olock:
                me = len(order)
                order.append(1)
            if me == 0:
                real_replace(src, dst)          # winner installs
            else:
                time.sleep(0.1)                 # let the winner's swap land first
                real_replace(src, dst)          # ENOTEMPTY under the un-fixed code
            return
        return real_replace(src, dst)

    monkeypatch.setattr(harness_lifecycle.os, "replace", gated_replace)

    errors = {}

    def worker(i):
        try:
            harness_lifecycle._install_version(real_courier)
        except Exception as e:  # noqa: BLE001
            errors[i] = "%s: %s" % (type(e).__name__, e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=20)
    assert not errors, "a concurrent first-install racer failed: %s" % errors
    assert (version_dir / "harness" / "scripts" / "harness_paths.py").is_file()
    assert not harness_lifecycle._real_drift(version_dir), "installed engine did not verify clean"


def test_repoint_heals_forward_after_stale_floor_race(tmp_path, monkeypatch):
    """Round-27: the forward-only guard reads the floor then swaps with no lock, so a
    racer installing+repointing a NEWER version in that window could leave `current`
    clobbered to an OLDER version. The post-swap heal must re-read the newest installed
    engine and never leave `current` below it. Deterministic via a stale-then-real
    floor read (call 1 = guard sees a stale older floor; call 2 = heal sees the truth)."""
    eh = tmp_path / "engine"
    for v in ("5.0.0", "6.0.0"):
        (eh / v / "harness").mkdir(parents=True)
        (eh / v / "harness" / "VERSION").write_text(v)
        (eh / v / "harness" / "release.json").write_text('{"harness_version": "%s"}' % v)
    # start with `current` -> 6.0.0 (the newest), as a healthy home would have
    (eh / ".current.tmp.seed").symlink_to("6.0.0")
    os.replace(eh / ".current.tmp.seed", eh / "current")

    real_floor = harness_lifecycle._home_floor_version
    calls = {"n": 0}

    def stale_then_real(engine_home):
        calls["n"] += 1
        return "5.0.0" if calls["n"] == 1 else real_floor(engine_home)  # stale guard, real heal

    monkeypatch.setattr(harness_lifecycle, "_home_floor_version", stale_then_real)
    harness_lifecycle._repoint_current(eh, "5.0.0")  # a racy repoint to the OLDER version
    assert (eh / "current").resolve().name == "6.0.0", \
        "current was left below the newest installed engine (stale-floor race not healed)"


def test_repoint_heals_current_up_when_refusing_older_than_pinned_floor(tmp_path):
    """Round-28: a NEWER version installed via `--pin` raises the floor without
    advancing `current`. A later non-pin repoint of a MIDDLE version is refused by the
    forward-only guard — but `current` must NOT be left stranded below the newest
    installed engine (a silent effective downgrade: the repo would run an older engine
    than it just installed, with no in-tool remediation). It must heal UP to the floor."""
    eh = tmp_path / "engine"
    for v in ("3.9.0", "4.0.1", "4.0.2"):
        (eh / v / "harness").mkdir(parents=True)
        (eh / v / "harness" / "VERSION").write_text(v)
        (eh / v / "harness" / "release.json").write_text('{"harness_version": "%s"}' % v)
    (eh / ".seed").symlink_to("3.9.0")           # current stale at 3.9.0
    os.replace(eh / ".seed", eh / "current")
    # 4.0.2 is the pinned newest (installed dir, current NOT advanced); 4.0.1 is a
    # middle non-pin repoint the guard will refuse (4.0.1 < floor 4.0.2).
    harness_lifecycle._repoint_current(eh, "4.0.1")
    assert (eh / "current").resolve().name == "4.0.2", \
        "current left below the newest installed engine after a refused older repoint"


def test_setup_from_older_cache_does_not_downgrade_current(real_courier, target_repo, fake_home, tmp_path):
    """Round-6: a `setup` from a project whose plugin cache is OLDER must not roll
    the shared `current` back (it would downgrade every other project's engine)."""
    harness_lifecycle.setup(target_repo, source_engine=real_courier)
    eh = fake_home / ".local" / "share" / "harness"
    v_new = _version_of(real_courier)
    assert (eh / "current").resolve() == (eh / v_new).resolve()

    older = _older_cache(real_courier, tmp_path, "0.0.1")
    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    _git(repo2, "init", "-q")
    _git(repo2, "config", "user.email", "t@t")
    _git(repo2, "config", "user.name", "t")
    harness_lifecycle.setup(repo2, source_engine=older)

    assert (eh / "current").resolve() == (eh / v_new).resolve(), \
        "setup from an older cache silently downgraded the shared current"
    assert (eh / "0.0.1" / "harness").is_dir(), "the older version was not installed side-by-side"


def test_setup_pin_does_not_touch_current(real_courier, target_repo, fake_home):
    """Round-6: --pin wires the repo to the version dir and must NOT touch the shared
    `current` (its stated contract: 'not the moving current')."""
    harness_lifecycle.setup(target_repo, source_engine=real_courier, pin=True)
    eh = fake_home / ".local" / "share" / "harness"
    assert not (eh / "current").exists(), "setup --pin touched the shared current pointer"
    # the version IS installed
    assert (eh / _version_of(real_courier) / "harness").is_dir()


def test_non_pin_setup_from_older_cache_wires_from_current(real_courier, target_repo, fake_home, tmp_path):
    """Round-7: with forward-only, a non-pin setup from an OLDER cache leaves
    `current` at the newer version — so the repo must be wired from `current`, NOT
    the older version_dir, or it gets the wrong (dropped/missing) hook set."""
    harness_lifecycle.setup(target_repo, source_engine=real_courier)  # current = V_new
    older = _older_cache(real_courier, tmp_path, "0.0.1")
    # give the older cache a DISTINCTIVE registration entry current does not have,
    # then regen its manifest so the modified copy still verifies.
    reg = older / "harness" / "install" / "hooks-registration.yaml"
    reg.write_text(reg.read_text() +
                   "\n  - event: SessionStart\n"
                   "    command: $HARNESS_PY $HARNESS_ROOT/harness/hooks/__sentinel_v1_only__.py\n"
                   "    class: telemetry\n")
    _git(older, "init", "-q")
    _git(older, "config", "user.email", "t@t")
    _git(older, "config", "user.name", "t")
    _git(older, "add", "-A")
    _git(older, "commit", "-qm", "s")
    build_manifest.main(["--root", str(older)])

    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    _git(repo2, "init", "-q")
    _git(repo2, "config", "user.email", "t@t")
    _git(repo2, "config", "user.name", "t")
    harness_lifecycle.setup(repo2, source_engine=older)  # non-pin; current stays V_new

    sl = (repo2 / ".claude" / "settings.local.json").read_text()
    assert "__sentinel_v1_only__" not in sl, \
        "hooks wired from the older version_dir instead of the resolved `current` engine"


def test_doctor_and_version_report_pin_only_home(real_courier, target_repo, fake_home):
    """Round-7: after a --pin-only setup there is no `current`; doctor/version must
    NOT claim 'no engine installed' — an engine IS installed, just not currented."""
    harness_lifecycle.setup(target_repo, source_engine=real_courier, pin=True)
    eh = fake_home / ".local" / "share" / "harness"
    assert not (eh / "current").exists()
    rc, rep = harness_lifecycle.doctor(source_engine=real_courier, capture=True)
    assert "no engine installed" not in rep
    assert "installed" in rep and "current" in rep
    info = harness_lifecycle.version_info(source_engine=real_courier)
    assert info["home_version"] == _version_of(real_courier)
    assert info["current_set"] is False


def test_upgrade_refuses_downgrade_on_pin_only_home(real_courier, target_repo, fake_home, tmp_path):
    """Round-8: the forward-only guard must key off the NEWEST installed version,
    not `current` — a pin-only home (no current) + upgrade from an older cache must
    still refuse, not create a current older than what is installed."""
    harness_lifecycle.setup(target_repo, source_engine=real_courier, pin=True)  # pin-only, no current
    eh = fake_home / ".local" / "share" / "harness"
    assert not (eh / "current").exists()
    older = _older_cache(real_courier, tmp_path, "0.0.1")
    with pytest.raises(harness_lifecycle.LifecycleError) as ei:
        harness_lifecycle.upgrade(source_engine=older)
    assert "downgrade" in str(ei.value).lower()
    assert not (eh / "current").exists(), "a refused upgrade created a current pointer"


def test_doctor_honors_harness_bin_root_for_pinned_repo(real_courier, target_repo, fake_home, tmp_path, monkeypatch):
    """Round-8: doctor must verify the engine THIS repo resolves (HARNESS_BIN_ROOT),
    not blindly `current` — else a pinned repo whose own engine is corrupt gets a
    false 'integrity: OK' (F8 blind spot)."""
    harness_lifecycle.setup(target_repo, source_engine=real_courier)  # current = V_new (clean)
    eh = fake_home / ".local" / "share" / "harness"
    # install an older version pinned + then corrupt it (current stays V_new, clean)
    older = _older_cache(real_courier, tmp_path, "0.0.1")
    harness_lifecycle.setup(tmp_path / "repo_pin", source_engine=older, pin=True) \
        if (tmp_path / "repo_pin").mkdir() or True else None
    victim = eh / "0.0.1" / "harness" / "scripts" / "harness_paths.py"
    victim.write_text(victim.read_text() + "\n# tampered\n")

    # doctor with HARNESS_BIN_ROOT pointed at the corrupt pinned engine
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(eh / "0.0.1"))
    rc, rep = harness_lifecycle.doctor(source_engine=older, capture=True)
    assert "drift" in rep.lower() and rc == 1, \
        "doctor gave a clean bill of health for a corrupt pinned engine: %r" % rep
    assert "harness_paths.py" in rep


def _place_version(eh, version, *, current=False, valid=True):
    import hashlib
    vd = eh / version
    (vd / "harness" / "hooks").mkdir(parents=True)
    payload = b"engine bytes"
    (vd / "harness" / "x.txt").write_bytes(payload)
    if valid:
        (vd / "harness" / "manifest.json").write_text(json.dumps(
            {"files": {"harness/x.txt": hashlib.sha256(payload).hexdigest()}}))
    (vd / "harness" / "release.json").write_text(json.dumps({"harness_version": version}))
    if current:
        (eh / "current").symlink_to(version)
    return vd


def test_doctor_reports_corrupt_resolved_engine_without_manifest(fake_home, monkeypatch):
    """Round-9 F1: a resolved (pinned) engine with NO manifest is corrupt/incomplete
    — doctor must report rc=1, not fall to the pin-only 'healthy home' message."""
    eh = fake_home / ".local" / "share" / "harness"
    eh.mkdir(parents=True)
    _place_version(eh, "5.0.0", current=True, valid=True)
    vd3 = _place_version(eh, "3.0.0", valid=True)
    (vd3 / "harness" / "manifest.json").unlink()  # corrupt/incomplete pinned engine
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(eh / "3.0.0"))
    rc, rep = harness_lifecycle.doctor(capture=True)
    assert rc == 1, rep
    assert "no manifest" in rep.lower() or "corrupt" in rep.lower()


def test_doctor_skew_uses_max_installed_not_current(fake_home, tmp_path, monkeypatch):
    """Round-9 F2: skew advice must key off max-installed (the reference the guards
    enforce), so doctor never advises `harness upgrade` for a version upgrade refuses."""
    monkeypatch.delenv("HARNESS_BIN_ROOT", raising=False)
    eh = fake_home / ".local" / "share" / "harness"
    eh.mkdir(parents=True)
    _place_version(eh, "3.0.0", current=True, valid=True)
    _place_version(eh, "5.0.0", valid=True)  # pinned newer; current stays 3.0.0
    # a cache ENGINE dir (harness/ directly, version 4.0.0 — between the two)
    cache = tmp_path / "cache"
    (cache / "harness").mkdir(parents=True)
    (cache / "harness" / "release.json").write_text(json.dumps({"harness_version": "4.0.0"}))
    rc, rep = harness_lifecycle.doctor(source_engine=cache, capture=True)
    assert "run `harness upgrade` to apply" not in rep, \
        "doctor advised an upgrade that upgrade() would refuse: %r" % rep
    assert "5.0.0" in rep  # the note references the TRUE floor (max-installed)


def test_non_pin_setup_onto_pin_only_newer_home_no_crash(real_courier, target_repo, fake_home, tmp_path):
    """Round-12 #1 (BLOCKING): forward-only can leave NO `current` (a non-pin setup
    of a version OLDER than one already pinned). setup must still establish `current`
    at the newest installed engine, not crash on a dangling `current` path."""
    # pin the NEWER version first → pin-only home (no current)
    harness_lifecycle.setup(target_repo, source_engine=real_courier, pin=True)
    eh = fake_home / ".local" / "share" / "harness"
    v_new = _version_of(real_courier)
    assert not (eh / "current").exists()

    older = _older_cache(real_courier, tmp_path, "0.0.1")
    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    _git(repo2, "init", "-q")
    _git(repo2, "config", "user.email", "t@t")
    _git(repo2, "config", "user.name", "t")
    # this must NOT raise a raw FileNotFoundError
    res = harness_lifecycle.setup(repo2, source_engine=older)
    # `current` is now established at the NEWEST installed engine (not the older 0.0.1)
    assert (eh / "current").is_symlink()
    assert (eh / "current").resolve() == (eh / v_new).resolve()
    assert (repo2 / ".claude" / "settings.local.json").is_file()
