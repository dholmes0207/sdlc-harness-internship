"""test_cleanup_orphans — safe orphan cleanup for over-install.

The planner is pure (no fs writes) and reuses verify_install.orphan_problems as
its disk-scan primitive (no second scanner). The apply step is the only fs
mutator: it backs up EVERYTHING before deleting anything (atomic), unlinks
symlinks without following them, and never touches harness/state/.
"""
import hashlib
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cleanup_orphans as co  # noqa: E402
import verify_install  # noqa: E402


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _mk_target(root: Path, new_manifest_files: dict, disk: dict):
    """Build a target tree: harness/manifest.json holds new_manifest_files;
    `disk` maps rel-path -> bytes written under root."""
    (root / "harness").mkdir(parents=True, exist_ok=True)
    import json
    (root / "harness" / "manifest.json").write_text(
        json.dumps({"files": new_manifest_files}), encoding="utf-8")
    for rel, content in disk.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


# ---- planner -----------------------------------------------------------------

def test_plan_classifies_pristine_vs_modified(tmp_path):
    old = {"files": {"harness/foo.py": _sha(b"old"),
                     "harness/bar.py": _sha(b"orig")}}
    # both dropped from the NEW manifest -> orphans on disk
    _mk_target(tmp_path, new_manifest_files={},
               disk={"harness/foo.py": b"old",        # pristine -> REMOVE
                     "harness/bar.py": b"changed"})    # modified -> PROMPT
    plan = co.plan_cleanup(tmp_path, old)
    assert "harness/foo.py" in plan["remove"]
    assert "harness/bar.py" in plan["prompt"]
    assert "harness/foo.py" not in plan["prompt"]


def test_plan_keeps_user_added(tmp_path):
    old = {"files": {"harness/foo.py": _sha(b"old")}}
    _mk_target(tmp_path, new_manifest_files={},
               disk={"harness/foo.py": b"old",
                     "harness/mine.txt": b"my notes"})   # not in old manifest
    plan = co.plan_cleanup(tmp_path, old)
    assert "harness/mine.txt" in plan["keep_user"]
    assert "harness/mine.txt" not in plan["remove"]


def test_plan_keeps_user_symlink(tmp_path):
    """A symlink the harness never shipped (absent from old + new manifest) is
    user data — KEEP it, never unlink. Only version-dropped symlinks (in the old
    manifest, gone from the new) are orphans we may remove."""
    _mk_target(tmp_path, new_manifest_files={}, disk={})
    (tmp_path / "external").write_bytes(b"my config")
    os.symlink(tmp_path / "external", tmp_path / "harness/my_link")
    plan = co.plan_cleanup(tmp_path, {"files": {}})   # nothing was ever shipped
    assert "harness/my_link" in plan["keep_user"]
    assert "harness/my_link" not in plan["unlink"]


def test_plan_refuses_when_new_manifest_unreadable(tmp_path):
    """No ground truth (manifest missing/corrupt) = cannot tell shipped from
    orphan → refuse rather than treat everything as removable."""
    (tmp_path / "harness").mkdir(parents=True)
    (tmp_path / "harness/manifest.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        co.plan_cleanup(tmp_path, {"files": {"harness/x.py": _sha(b"x")}})


def test_plan_excludes_state(tmp_path):
    old = {"files": {"harness/state/old.log": _sha(b"x")}}
    _mk_target(tmp_path, new_manifest_files={},
               disk={"harness/state/old.log": b"x"})
    plan = co.plan_cleanup(tmp_path, old)
    flat = sum((plan[k] for k in plan), [])
    assert not any(r.startswith("harness/state/") for r in flat)


def test_first_install_noop(tmp_path):
    _mk_target(tmp_path, new_manifest_files={}, disk={"harness/foo.py": b"x"})
    plan = co.plan_cleanup(tmp_path, None)   # no old manifest = first install
    assert all(plan[k] == [] for k in plan)


# ---- apply -------------------------------------------------------------------

def test_apply_backs_up_before_delete(tmp_path):
    _mk_target(tmp_path, new_manifest_files={}, disk={"harness/foo.py": b"old"})
    plan = {"remove": ["harness/foo.py"], "prompt": [], "keep_user": [],
            "keep_exception": [], "unlink": []}
    backups = tmp_path / "backups"
    result = co.apply_cleanup(plan, tmp_path, backups)
    # target gone, a backup copy with identical bytes remains
    assert not (tmp_path / "harness/foo.py").exists()
    bdir = Path(result["backup_dir"])
    assert (bdir / "harness/foo.py").read_bytes() == b"old"


def test_apply_atomic_rollback(tmp_path, monkeypatch):
    _mk_target(tmp_path, new_manifest_files={},
               disk={"harness/a.py": b"a", "harness/b.py": b"b"})
    plan = {"remove": ["harness/a.py", "harness/b.py"], "prompt": [],
            "keep_user": [], "keep_exception": [], "unlink": []}
    calls = {"n": 0}
    real_unlink = co._unlink

    def boom(path):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk died mid-delete")
        return real_unlink(path)

    monkeypatch.setattr(co, "_unlink", boom)
    with pytest.raises(RuntimeError):
        co.apply_cleanup(plan, tmp_path, tmp_path / "backups")
    # everything that was deleted got restored -> both files present again
    assert (tmp_path / "harness/a.py").read_bytes() == b"a"
    assert (tmp_path / "harness/b.py").read_bytes() == b"b"


def test_rollback_when_backup_fails_midway(tmp_path, monkeypatch):
    _mk_target(tmp_path, new_manifest_files={},
               disk={"harness/a.py": b"a", "harness/b.py": b"b"})
    plan = {"remove": ["harness/a.py", "harness/b.py"], "prompt": [],
            "keep_user": [], "keep_exception": [], "unlink": []}
    calls = {"n": 0}
    real_copy = co._copy

    def boom(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full mid-backup")
        return real_copy(src, dst)

    monkeypatch.setattr(co, "_copy", boom)
    with pytest.raises(RuntimeError):
        co.apply_cleanup(plan, tmp_path, tmp_path / "backups")
    # backup failed before any delete -> both targets intact, nothing lost
    assert (tmp_path / "harness/a.py").read_bytes() == b"a"
    assert (tmp_path / "harness/b.py").read_bytes() == b"b"


def test_symlink_unlink_not_follow(tmp_path):
    # external targets the symlinks point at (outside the harness tree)
    ext_file = tmp_path / "ext_file"
    ext_file.write_bytes(b"keepme")
    ext_dir = tmp_path / "ext_dir"
    ext_dir.mkdir()
    (ext_dir / "inside").write_bytes(b"alsokeep")

    _mk_target(tmp_path, new_manifest_files={}, disk={})
    os.symlink(ext_file, tmp_path / "harness/link_file")
    os.symlink(ext_dir, tmp_path / "harness/link_dir")
    os.symlink(tmp_path / "nope", tmp_path / "harness/link_broken")

    # version-dropped symlinks: shipped by the OLD version, gone from the new one.
    old = {"files": {"harness/link_file": _sha(b"shipped"),
                     "harness/link_dir": _sha(b"shipped"),
                     "harness/link_broken": _sha(b"shipped")}}
    plan = co.plan_cleanup(tmp_path, old)
    for rel in ("harness/link_file", "harness/link_dir", "harness/link_broken"):
        assert rel in plan["unlink"], rel

    co.apply_cleanup(plan, tmp_path, tmp_path / "backups")
    # links gone, external targets untouched
    assert not os.path.lexists(tmp_path / "harness/link_file")
    assert not os.path.lexists(tmp_path / "harness/link_dir")
    assert ext_file.read_bytes() == b"keepme"
    assert (ext_dir / "inside").read_bytes() == b"alsokeep"


def test_rollback_continues_when_one_restore_fails(tmp_path, monkeypatch):
    """If a single restore raises mid-rollback, the rest must still be restored —
    one bad file may not strand the others (they survive in the backup either way)."""
    _mk_target(tmp_path, new_manifest_files={},
               disk={"harness/a.py": b"a", "harness/b.py": b"b", "harness/c.py": b"c"})
    plan = {"remove": ["harness/a.py", "harness/b.py", "harness/c.py"], "prompt": [],
            "keep_user": [], "keep_exception": [], "unlink": []}
    # fail the 3rd delete so a + b are already deleted and must roll back
    real_unlink = co._unlink
    ucalls = {"n": 0}

    def boom_unlink(path):
        ucalls["n"] += 1
        if ucalls["n"] == 3:
            raise RuntimeError("disk died mid-delete")
        return real_unlink(path)

    # and fail restoring a.py — b.py must still come back
    real_restore = co._restore

    def boom_restore(backup_root, target_root, rel, kind):
        if rel == "harness/a.py":
            raise RuntimeError("restore of a failed")
        return real_restore(backup_root, target_root, rel, kind)

    monkeypatch.setattr(co, "_unlink", boom_unlink)
    monkeypatch.setattr(co, "_restore", boom_restore)
    with pytest.raises(Exception):
        co.apply_cleanup(plan, tmp_path, tmp_path / "backups")
    assert (tmp_path / "harness/b.py").read_bytes() == b"b"  # not stranded by a's failure


def test_backup_dir_unique_same_second(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "_timestamp", lambda: "20260624-120000")
    _mk_target(tmp_path, new_manifest_files={}, disk={"harness/a.py": b"a"})
    plan1 = {"remove": ["harness/a.py"], "prompt": [], "keep_user": [],
             "keep_exception": [], "unlink": []}
    r1 = co.apply_cleanup(plan1, tmp_path, tmp_path / "backups")
    # recreate the file and run again in the same simulated second
    (tmp_path / "harness/a.py").write_bytes(b"a")
    r2 = co.apply_cleanup(plan1, tmp_path, tmp_path / "backups")
    assert r1["backup_dir"] != r2["backup_dir"]
