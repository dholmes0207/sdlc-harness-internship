"""test_hs_cli_cleanup — the manual cleanup door (hs-cli cleanup + hs:cleanup).

The CLI re-runs the engine and surfaces the PROMPT layer; the skill wraps it with
an interactive Keep/Change. Also pins the --prune reconcile (the old coarse path
still works) and the narrow-reinstall case (a dir already removed isn't touched
again).
"""
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness/scripts"
_SKILL_DIR = _ROOT / "harness/plugins/hs/skills/cleanup"
_INSTALL_DIR = _ROOT / "harness/install"
for _p in (str(_SCRIPTS), str(_INSTALL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hs_cli  # noqa: E402


def _mk_target(root, new_files, disk, old_files):
    (root / "harness").mkdir(parents=True, exist_ok=True)
    (root / "harness/manifest.json").write_text(json.dumps({"files": new_files}))
    for rel, content in disk.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    snap = root / "old-manifest.json"
    snap.write_text(json.dumps({"files": old_files}))
    return snap


def test_hs_cli_cleanup_lists_prompt_layer(tmp_path, capsys):
    snap = _mk_target(
        tmp_path, new_files={}, disk={"harness/mod.py": b"changed"},
        old_files={"harness/mod.py": hashlib.sha256(b"orig").hexdigest()})
    rc = hs_cli.main(["cleanup", "--root", str(tmp_path),
                      "--old-manifest", str(snap), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "prompt" in out and "harness/mod.py" in out


def test_hs_cli_cleanup_uses_persisted_snapshot(tmp_path, capsys):
    """A manual re-run with no --old-manifest falls back to the durable snapshot
    install.sh persisted under harness/state/ — otherwise the deferred prompt
    layer is unreachable."""
    (tmp_path / "harness/state").mkdir(parents=True)
    (tmp_path / "harness/manifest.json").write_text(json.dumps({"files": {}}))
    (tmp_path / "harness/mod.py").write_bytes(b"changed")
    (tmp_path / "harness/state/cleanup-prev-manifest.json").write_text(
        json.dumps({"files": {"harness/mod.py": hashlib.sha256(b"orig").hexdigest()}}))
    rc = hs_cli.main(["cleanup", "--root", str(tmp_path), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "prompt" in out and "harness/mod.py" in out


def test_hs_cli_cleanup_remove_promotes_modified(tmp_path):
    """--remove promotes a modified (prompt-layer) file to removal, headless."""
    snap = _mk_target(
        tmp_path, new_files={}, disk={"harness/mod.py": b"changed"},
        old_files={"harness/mod.py": hashlib.sha256(b"orig").hexdigest()})
    rc = hs_cli.main(["cleanup", "--root", str(tmp_path), "--old-manifest", str(snap),
                      "--apply", "--remove", "harness/mod.py"])
    assert rc == 0
    assert not (tmp_path / "harness/mod.py").exists()  # promoted + removed
    # a backup copy survives under state/cleanup-backup
    backups = list((tmp_path / "harness/state/cleanup-backup").glob("*/harness/mod.py"))
    assert backups and backups[0].read_bytes() == b"changed"


def test_hs_cli_cleanup_remove_warns_on_unmatched(tmp_path, capsys):
    """--remove of a path not in the prompt layer must NOT be a silent no-op —
    the user could wrongly believe a file was cleaned."""
    snap = _mk_target(
        tmp_path, new_files={}, disk={"harness/mod.py": b"changed"},
        old_files={"harness/mod.py": hashlib.sha256(b"orig").hexdigest()})
    rc = hs_cli.main(["cleanup", "--root", str(tmp_path), "--old-manifest", str(snap),
                      "--apply", "--remove", "harness/typo.py"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "harness/typo.py" in out and "prompt" in out.lower()
    assert (tmp_path / "harness/mod.py").exists()  # nothing actually promoted


def test_hs_cli_cleanup_dry_run_overrides_apply(tmp_path):
    """--dry-run is explicit intent; it wins over a co-passed --apply (no writes)."""
    snap = _mk_target(
        tmp_path, new_files={}, disk={"harness/foo.py": b"old"},
        old_files={"harness/foo.py": hashlib.sha256(b"old").hexdigest()})
    rc = hs_cli.main(["cleanup", "--root", str(tmp_path), "--old-manifest", str(snap),
                      "--apply", "--dry-run"])
    assert rc == 0
    assert (tmp_path / "harness/foo.py").exists()  # pristine orphan NOT removed


def test_prune_still_works(tmp_path):
    """install.py --prune still removes orphans coarsely (coarse reconcile)."""
    import install as install_mod
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness/manifest.json").write_text(json.dumps({"files": {}}))
    orphan = tmp_path / "harness/stale.py"
    orphan.write_bytes(b"stale")
    result = {"actions": [], "warnings": []}
    install_mod._prune_orphans(tmp_path, result, dry_run=False)
    assert not orphan.exists(), "prune should have removed the orphan"


def test_narrow_reinstall_no_double_delete(tmp_path):
    """A file already gone from disk (narrowed skill) is never re-deleted."""
    import cleanup_orphans as co
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness/manifest.json").write_text(json.dumps({"files": {}}))
    # old manifest lists a file that no longer exists on disk
    old = {"files": {"harness/plugins/hs/skills/gone/SKILL.md":
                     hashlib.sha256(b"x").hexdigest()}}
    plan = co.plan_cleanup(tmp_path, old)
    flat = sum((plan[k] for k in plan), [])
    assert "harness/plugins/hs/skills/gone/SKILL.md" not in flat
    # apply over an empty plan is a clean no-op
    res = co.apply_cleanup(plan, tmp_path, tmp_path / "backups")
    assert res["backup_dir"] is None
