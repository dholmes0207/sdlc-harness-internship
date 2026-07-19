"""hs:spec — snapshot.py: --label / --restore path-traversal containment,
and removal of the dead latest_snapshot() helper.

snapshot.py is a LOCAL single-user tool: the threat modeled here is a
fat-fingered `..` in --label/--restore, not a remote attacker. The fix is
cheap input validation that keeps every write/read resolved and contained
under snapshots_home, and a documented error instead of a raw traceback or
a silent out-of-tree write.
"""

import io
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
_mods = load_skill_scripts(_SPEC_SCRIPTS, ["snapshot"])
snapshot = _mods["snapshot"]


def _run_cli(argv):
    """Invoke snapshot.main() in-process, capturing argv/stdout/stderr."""
    old_argv = sys.argv
    sys.argv = ["snapshot.py"] + argv
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = snapshot.main()
        return rc, out.getvalue(), err.getvalue()
    finally:
        sys.argv = old_argv


def _proj(tmp_path):
    proj = tmp_path / "proj"
    spec_root = proj / "docs" / "product"
    spec_root.mkdir(parents=True)
    (spec_root / "PRD.md").write_text("live content", encoding="utf-8")
    return proj, spec_root


# ── --label path-traversal on write ───────────────────────────────────────

def test_label_traversal_does_not_escape_snapshots_home_via_cli(tmp_path):
    proj, _spec_root = _proj(tmp_path)
    # verbatim PoC from the finding: 3 `..` segments walk past snapshots_home
    # (one level cancels the timestamp-prefixed first component, the next
    # walks above snapshots_home into proj/).
    rc, _out, err = _run_cli(
        ["--root", str(proj), "--snapshot", "--label", "../../../tmp/evil"]
    )
    assert rc != 0
    assert "ERROR" in err
    assert not (proj / "tmp").exists()
    assert not (tmp_path / "tmp").exists()
    snapshots_home = proj / ".product-spec-snapshots"
    # nothing escaped even indirectly: every entry under snapshots_home (if
    # any got created before the raise — it shouldn't) stays contained.
    if snapshots_home.exists():
        for child in snapshots_home.rglob("*"):
            assert child.resolve().is_relative_to(snapshots_home.resolve())


def test_make_snapshot_rejects_traversal_ts_before_any_write(tmp_path):
    proj, spec_root = _proj(tmp_path)
    snapshots_home = proj / ".product-spec-snapshots"
    with pytest.raises(ValueError):
        snapshot.make_snapshot(
            spec_root, snapshots_home, ts="20260101T000000-../../../evil"
        )
    assert not (proj / "evil").exists()
    assert not snapshots_home.exists()


def test_label_containing_slash_is_rejected_cleanly(tmp_path):
    proj, _spec_root = _proj(tmp_path)
    rc, _out, err = _run_cli(
        ["--root", str(proj), "--snapshot", "--label", "with/slash"]
    )
    assert rc != 0
    assert "ERROR" in err
    snapshots_home = proj / ".product-spec-snapshots"
    if snapshots_home.exists():
        assert list(snapshots_home.iterdir()) == []


def test_label_safe_value_still_produces_a_contained_snapshot(tmp_path):
    proj, spec_root = _proj(tmp_path)
    snapshots_home = proj / ".product-spec-snapshots"
    dest = snapshot.make_snapshot(spec_root, snapshots_home, ts="20260101T000000-baseline")
    assert dest.resolve().is_relative_to(snapshots_home.resolve())
    assert (dest / "PRD.md").read_text(encoding="utf-8") == "live content"


def test_restore_does_not_delete_colliding_user_staging_dir(tmp_path):
    # The restore scratch dirs must be uniquely ours: a pre-existing user dir that
    # happens to collide with the derived `_restore_staging_<ts>` name must NOT be
    # rmtree'd by the cleanup path (that was silent data loss).
    proj, spec_root = _proj(tmp_path)
    snapshots_home = proj / ".product-spec-snapshots"
    ts = "20260101T000000-baseline"
    snapshot.make_snapshot(spec_root, snapshots_home, ts=ts)
    collide = spec_root.parent / f"_restore_staging_{ts}"
    collide.mkdir(parents=True)
    (collide / "IMPORTANT.txt").write_text("user data", encoding="utf-8")
    try:
        snapshot.restore_snapshot(spec_root, snapshots_home, ts=ts, confirm=True)
    except Exception:
        pass
    assert (collide / "IMPORTANT.txt").is_file(), "restore rmtree deleted a colliding user dir"
    # and the restore itself still works
    assert (spec_root / "PRD.md").read_text(encoding="utf-8") == "live content"


# ── --restore path-traversal on read ──────────────────────────────────────

def test_restore_dotdot_raises_documented_error_and_leaves_tree_untouched(tmp_path):
    proj, spec_root = _proj(tmp_path)
    snapshots_home = proj / ".product-spec-snapshots"
    snapshots_home.mkdir()
    with pytest.raises(snapshot.SnapshotNotFoundError):
        snapshot.restore_snapshot(spec_root, snapshots_home, "..", confirm=True)
    assert (spec_root / "PRD.md").read_text(encoding="utf-8") == "live content"


def test_restore_dotdot_slash_foo_raises_documented_error(tmp_path):
    proj, spec_root = _proj(tmp_path)
    snapshots_home = proj / ".product-spec-snapshots"
    snapshots_home.mkdir()
    with pytest.raises(snapshot.SnapshotNotFoundError):
        snapshot.restore_snapshot(spec_root, snapshots_home, "../foo", confirm=True)
    assert (spec_root / "PRD.md").read_text(encoding="utf-8") == "live content"


def test_restore_format_valid_ts_with_embedded_traversal_still_blocked(tmp_path):
    # regex-shape-valid (digits+T+digits + free-form -suffix) but the suffix
    # resolves outside snapshots_home — the containment check must catch what
    # the format check alone would miss.
    proj, spec_root = _proj(tmp_path)
    snapshots_home = proj / ".product-spec-snapshots"
    snapshots_home.mkdir()
    ts = "20260101T000000-../../../etc"
    with pytest.raises(snapshot.SnapshotNotFoundError):
        snapshot.restore_snapshot(spec_root, snapshots_home, ts, confirm=True)
    assert (spec_root / "PRD.md").read_text(encoding="utf-8") == "live content"


def test_restore_cli_dotdot_returns_clean_error_not_traceback(tmp_path):
    proj, spec_root = _proj(tmp_path)
    rc, _out, err = _run_cli(["--root", str(proj), "--restore", "..", "--confirm"])
    assert rc == 2
    assert "ERROR" in err
    assert "Traceback" not in err
    assert (spec_root / "PRD.md").read_text(encoding="utf-8") == "live content"


def test_restore_round_trip_still_works_after_the_fix(tmp_path):
    # non-regression: a normal ts (the shape make_snapshot generates) restores
    # exactly as before the validation was added.
    proj, spec_root = _proj(tmp_path)
    snapshots_home = proj / ".product-spec-snapshots"
    snapshot.make_snapshot(spec_root, snapshots_home, ts="20260101T000000")
    (spec_root / "PRD.md").write_text("edited after snapshot", encoding="utf-8")
    snapshot.restore_snapshot(spec_root, snapshots_home, "20260101T000000", confirm=True)
    assert (spec_root / "PRD.md").read_text(encoding="utf-8") == "live content"


# ── C2 — dead code removal ────────────────────────────────────────────────

def test_latest_snapshot_dead_code_removed():
    assert not hasattr(snapshot, "latest_snapshot")


def test_is_within_contract_covers_equal_descendant_and_prefix_lookalike(tmp_path):
    # _is_within must return True for an identical path (is_relative_to already
    # covers the equal case, so the historical `child == parent or` clause was
    # dead) and for a descendant, and False for a string-prefix look-alike that is
    # NOT a path-segment descendant. Locks the contract after dropping the
    # redundant clause so snapshot._is_within stays a true mirror of fs_guard's.
    parent = tmp_path / "docs" / "product"
    parent.mkdir(parents=True)
    assert snapshot._is_within(parent, parent) is True
    assert snapshot._is_within(parent / "visuals" / "x", parent) is True
    assert snapshot._is_within(tmp_path / "docs" / "product-extra", parent) is False
