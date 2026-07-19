"""courier_tree.py — build the plugin courier tree from a SCRUBBED pack tarball.

Red-team F1 lock: the builder's ONLY input is a tarball produced by
release/pack.build_tarball (already scrub-transformed). Handing it the live repo
directory must be refused by mechanism, so a dev's empty protected-branches floor
or accumulated LESSONS entries can never leak into a published payload.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
_INSTALL = _REPO_ROOT / "harness" / "install"
_RELEASE = _REPO_ROOT / "release"
for _p in (str(_SCRIPTS), str(_INSTALL), str(_RELEASE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_manifest  # noqa: E402
import pack  # noqa: E402
import courier_tree  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, check=True)


@pytest.fixture()
def mini_tarball(tmp_path):
    """A tiny scrubbed pack tarball: a few harness/ files incl. tests/ and a
    stashed disabled-skill (both must be dropped from the courier tree), a
    dev-empty protected floor + a LESSONS with a leaked entry (both must be
    scrubbed back to the shipped default by pack.build_tarball)."""
    root = tmp_path / "repo"
    (root / "harness" / "scripts").mkdir(parents=True)
    (root / "harness" / "data").mkdir(parents=True)
    (root / "harness" / "hooks").mkdir(parents=True)
    (root / "harness" / "tests").mkdir(parents=True)
    (root / "harness" / "plugins" / "hs" / "disabled-skills").mkdir(parents=True)

    (root / "harness" / "scripts" / "harness_paths.py").write_text("# paths\n")
    (root / "harness" / "hooks" / "a_hook.py").write_text("# hook\n")
    (root / "harness" / "bin").mkdir(parents=True)
    (root / "harness" / "bin" / "harness").write_text("#!/bin/sh\necho dispatcher\n")
    # dev-empty floor — pack must restore the protective default
    (root / "harness" / "data" / "protected-branches.yaml").write_text("protected: []\n")
    # LESSONS with a leaked dev entry BELOW the `---` divider — pack seeds the
    # bundle to everything up-to-and-including the divider, dropping the entry.
    (root / "harness" / "LESSONS.md").write_text(
        "# LESSONS\n\n## How to use this file\n\ntext\n\n## Entry template\n\ntext\n"
        "\n---\n\n## 2026-01-01 secret dev lesson\n\nleaked\n")
    (root / "harness" / "tests" / "test_x.py").write_text("def test_x():\n    pass\n")
    (root / "harness" / "plugins" / "hs" / "disabled-skills" / "off.md").write_text("stash\n")
    (root / "harness" / "release.json").write_text(json.dumps(
        {"channel": "stable", "harness_version": "9.9.9", "kit_digest": "deadbeef",
         "schema_version": "1.0"}, indent=2) + "\n")

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    build_manifest.main(["--root", str(root)])

    files = pack.manifest_files(root)
    out = tmp_path / "bundle.tar.gz"
    pack.build_tarball(root, files, out)
    return out


def test_refuse_directory_input(tmp_path, mini_tarball):
    """F1 mechanism: a directory (the live repo) is not a tarball -> refuse."""
    repo_dir = tmp_path / "repo"
    with pytest.raises((ValueError, IsADirectoryError, SystemExit)):
        courier_tree.build_courier_tree(repo_dir, tmp_path / "out", repo_root=tmp_path / "elsewhere")


def test_refuse_out_inside_repo(tmp_path, mini_tarball):
    repo_root = tmp_path / "myrepo"
    repo_root.mkdir()
    bad_out = repo_root / "sub" / "out"
    with pytest.raises((ValueError, SystemExit)):
        courier_tree.build_courier_tree(mini_tarball, bad_out, repo_root=repo_root)


def test_build_shape_and_scrubs(tmp_path, mini_tarball):
    out = tmp_path / "courier"
    courier_tree.build_courier_tree(mini_tarball, out, repo_root=tmp_path / "elsewhere")

    engine = out / "engine" / "harness"
    assert (engine / "scripts" / "harness_paths.py").is_file()
    assert (engine / "hooks" / "a_hook.py").is_file()
    assert (engine / "manifest.json").is_file()
    assert (engine / "release.json").is_file()

    # dropped from the courier tree
    assert not (engine / "tests").exists(), "harness/tests must not ship in the courier"
    assert not (engine / "plugins" / "hs" / "disabled-skills").exists(), \
        "disabled-skills stash must not ship"

    # scrub markers carried by the tarball survive into the tree
    prot = (engine / "data" / "protected-branches.yaml").read_text()
    assert "protected: []" not in prot, "dev-empty floor leaked (F1/F4)"
    assert "main" in prot, "protective default not restored"
    lessons = (engine / "LESSONS.md").read_text()
    assert "secret dev lesson" not in lessons, "leaked LESSONS entry survived"
    assert "How to use this file" in lessons

    # cache plugin.json: name 'harness', version from release.json
    pj = json.loads((out / ".claude-plugin" / "plugin.json").read_text())
    assert pj["name"] == "harness"
    assert pj["version"] == "9.9.9"

    # bin/ slot reserved for the phase-3 dispatcher
    assert (out / "bin").is_dir()


def test_manifest_filtered_and_kit_digest_synced(tmp_path, mini_tarball):
    """The courier drops tests/ + disabled-skills/ from disk, so the shipped
    manifest must drop them too (else verify_install reports them missing), and
    release.json's kit_digest = sha256(manifest.json) must track the filtered
    manifest."""
    import hashlib
    out = tmp_path / "courier"
    courier_tree.build_courier_tree(mini_tarball, out, repo_root=tmp_path / "elsewhere")
    engine = out / "engine" / "harness"

    manifest = json.loads((engine / "manifest.json").read_text())
    keys = list(manifest["files"].keys())
    assert not any(k.startswith("harness/tests/") for k in keys), \
        "manifest still lists dropped tests/"
    assert not any("disabled-skills" in k for k in keys), \
        "manifest still lists dropped disabled-skills"
    # every listed file must exist on disk (no missing-drift)
    for rel in keys:
        assert (out / "engine" / rel).is_file(), "manifest lists absent %s" % rel

    # kit_digest tracks the filtered manifest bytes
    release = json.loads((engine / "release.json").read_text())
    expect = hashlib.sha256((engine / "manifest.json").read_bytes()).hexdigest()
    assert release["kit_digest"] == expect, "kit_digest not synced to filtered manifest"


def test_bin_dispatcher_injected(tmp_path, mini_tarball):
    out = tmp_path / "courier"
    courier_tree.build_courier_tree(mini_tarball, out, repo_root=tmp_path / "elsewhere")
    disp = out / "bin" / "harness"
    assert disp.is_file(), "dispatcher not injected into <out>/bin/"
    import os
    assert os.access(disp, os.X_OK), "injected dispatcher is not executable"


def test_idempotent(tmp_path, mini_tarball):
    out = tmp_path / "courier"
    courier_tree.build_courier_tree(mini_tarball, out, repo_root=tmp_path / "elsewhere")
    files1 = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    courier_tree.build_courier_tree(mini_tarball, out, repo_root=tmp_path / "elsewhere")
    files2 = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert files1 == files2, "second build changed the tree — not idempotent"


def test_missing_release_json_actionable(tmp_path):
    # A tarball with no harness/release.json -> actionable error, not a raw traceback.
    import io, tarfile, gzip
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        payload = b"# paths\n"
        ti = tarfile.TarInfo("harness/scripts/harness_paths.py")
        ti.size = len(payload)
        tar.addfile(ti, io.BytesIO(payload))
    gz = tmp_path / "norelease.tar.gz"
    with gzip.open(gz, "wb") as g:
        g.write(buf.getvalue())
    with pytest.raises((ValueError, SystemExit)) as ei:
        courier_tree.build_courier_tree(gz, tmp_path / "out", repo_root=tmp_path / "elsewhere")
    assert "release.json" in str(ei.value).lower()


def test_e2e_dir_dropped(tmp_path, mini_tarball):
    """harness/e2e/ (slice scripts + a fixture with a nested plans/) is dev/CI
    weight and must not ship in the courier payload."""
    import subprocess as _sp
    # add an e2e/ tree to the mini source and repack
    # (the mini_tarball fixture has no e2e/; assert the DROP prefix works via a direct build)
    out = tmp_path / "courier"
    courier_tree.build_courier_tree(mini_tarball, out, repo_root=tmp_path / "elsewhere")
    assert "harness/e2e/" in courier_tree._DROP_PREFIXES
    # and no e2e/ shipped (mini has none, but the manifest filter must exclude the prefix)
    manifest = json.loads((out / "engine" / "harness" / "manifest.json").read_text())
    assert not any(k.startswith("harness/e2e/") for k in manifest["files"])


def test_empty_member_name_rejected(tmp_path):
    """Round-12 #3: a tarball member with an empty/'.' name collapses onto the
    staging dir; reject it as unsafe, not a raw IsADirectoryError."""
    import io, tarfile, gzip
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        ti = tarfile.TarInfo("")
        ti.size = 0
        tar.addfile(ti, io.BytesIO(b""))
        rel = b'{"harness_version": "9.9.9"}'
        ti2 = tarfile.TarInfo("harness/release.json")
        ti2.size = len(rel)
        tar.addfile(ti2, io.BytesIO(rel))
    gz = tmp_path / "bad.tar.gz"
    with gzip.open(gz, "wb") as g:
        g.write(buf.getvalue())
    with pytest.raises(courier_tree.CourierError):
        courier_tree.build_courier_tree(gz, tmp_path / "out", repo_root=tmp_path / "elsewhere")


def test_manifest_file_packed_as_symlink_rejected(tmp_path):
    """Round-12 #2: a manifest-listed path packed as a symlink is dropped by
    _safe_members, leaving the manifest lying. Fail loud instead of shipping it."""
    import io, tarfile, gzip, hashlib, json as _j
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        # manifest lists foo.py, but foo.py is packed as a SYMLINK (skipped on extract)
        manifest = {"files": {"harness/scripts/foo.py": hashlib.sha256(b"x").hexdigest()}}
        mb = _j.dumps(manifest).encode()
        mi = tarfile.TarInfo("harness/manifest.json"); mi.size = len(mb)
        tar.addfile(mi, io.BytesIO(mb))
        rel = b'{"harness_version": "9.9.9"}'
        ri = tarfile.TarInfo("harness/release.json"); ri.size = len(rel)
        tar.addfile(ri, io.BytesIO(rel))
        sl = tarfile.TarInfo("harness/scripts/foo.py")
        sl.type = tarfile.SYMTYPE; sl.linkname = "../release.json"
        tar.addfile(sl)
    gz = tmp_path / "swap.tar.gz"
    with gzip.open(gz, "wb") as g:
        g.write(buf.getvalue())
    with pytest.raises(courier_tree.CourierError) as ei:
        courier_tree.build_courier_tree(gz, tmp_path / "out", repo_root=tmp_path / "elsewhere")
    assert "not present on disk" in str(ei.value)


def test_manifest_traversal_key_rejected(tmp_path):
    """A manifest KEY with a traversal/absolute path (e.g. harness/../../etc/hostname)
    would make verify_install resolve `root / rel` to a file OUTSIDE the engine tree
    and hash it. _safe_members guards member NAMES; the finalize step must also guard
    manifest KEYS — fail loud, don't silently keep or drop."""
    import io, tarfile, gzip, json as _j
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        manifest = {"files": {"harness/../../etc/hostname": "0" * 64}}
        mb = _j.dumps(manifest).encode()
        mi = tarfile.TarInfo("harness/manifest.json"); mi.size = len(mb)
        tar.addfile(mi, io.BytesIO(mb))
        rel = b'{"harness_version": "9.9.9"}'
        ri = tarfile.TarInfo("harness/release.json"); ri.size = len(rel)
        tar.addfile(ri, io.BytesIO(rel))
    gz = tmp_path / "trav.tar.gz"
    with gzip.open(gz, "wb") as g:
        g.write(buf.getvalue())
    with pytest.raises(courier_tree.CourierError) as ei:
        courier_tree.build_courier_tree(gz, tmp_path / "out", repo_root=tmp_path / "elsewhere")
    assert "unsafe" in str(ei.value).lower()


def test_manifest_wrong_shape_rejected(tmp_path):
    """A valid-JSON but wrong-SHAPE manifest ('files' as a list, or a top-level list)
    must raise the module's actionable CourierError, not a raw AttributeError on
    .get()/.items() — the last unguarded poisoned-manifest vector."""
    import io, tarfile, gzip, json as _j
    for manifest_obj in ({"files": ["harness/release.json"]}, ["harness/release.json"]):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
            mb = _j.dumps(manifest_obj).encode()
            mi = tarfile.TarInfo("harness/manifest.json"); mi.size = len(mb)
            tar.addfile(mi, io.BytesIO(mb))
            rel = b'{"harness_version": "9.9.9"}'
            ri = tarfile.TarInfo("harness/release.json"); ri.size = len(rel)
            tar.addfile(ri, io.BytesIO(rel))
        gz = tmp_path / "shape.tar.gz"
        with gzip.open(gz, "wb") as g:
            g.write(buf.getvalue())
        with pytest.raises(courier_tree.CourierError) as ei:
            courier_tree.build_courier_tree(gz, tmp_path / "out", repo_root=tmp_path / "elsewhere")
        assert "not a json object" in str(ei.value).lower(), str(ei.value)
        (tmp_path / "out").exists() and __import__("shutil").rmtree(tmp_path / "out")


def test_release_json_invalid_json_rejected(tmp_path):
    """A truncated/corrupt release.json inside the tarball must raise CourierError, not
    a raw JSONDecodeError from build_courier_tree."""
    import io, tarfile, gzip
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        rel = b'{"harness_version": "9.9.9"'  # truncated — missing closing brace
        ri = tarfile.TarInfo("harness/release.json"); ri.size = len(rel)
        tar.addfile(ri, io.BytesIO(rel))
    gz = tmp_path / "relbad.tar.gz"
    with gzip.open(gz, "wb") as g:
        g.write(buf.getvalue())
    with pytest.raises(courier_tree.CourierError) as ei:
        courier_tree.build_courier_tree(gz, tmp_path / "out", repo_root=tmp_path / "elsewhere")
    assert "not valid json" in str(ei.value).lower(), str(ei.value)


def test_manifest_invalid_json_rejected(tmp_path):
    """A valid release.json but a truncated/corrupt manifest.json must raise CourierError
    from _finalize_engine_manifest — the json.loads must be guarded, not just the shape."""
    import io, tarfile, gzip
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        rel = b'{"harness_version": "9.9.9"}'
        ri = tarfile.TarInfo("harness/release.json"); ri.size = len(rel)
        tar.addfile(ri, io.BytesIO(rel))
        mb = b'{"files": {"harness/release.json": "abc"'  # truncated
        mi = tarfile.TarInfo("harness/manifest.json"); mi.size = len(mb)
        tar.addfile(mi, io.BytesIO(mb))
    gz = tmp_path / "manbad.tar.gz"
    with gzip.open(gz, "wb") as g:
        g.write(buf.getvalue())
    with pytest.raises(courier_tree.CourierError) as ei:
        courier_tree.build_courier_tree(gz, tmp_path / "out", repo_root=tmp_path / "elsewhere")
    assert "not valid json" in str(ei.value).lower(), str(ei.value)
