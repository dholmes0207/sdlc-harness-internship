"""test_fs_guard.py — script-path containment helper (ported HA/PS cases).

assert_under(path, zone) is the shared chokepoint keeping every SCRIPT-driven
harness write inside its declared zone root(s). Zones are DATA-DRIVEN from
harness/data/ownership.yaml — no hard-coded domain table. Resolve-then-contain
defeats `..` traversal, symlink escape, and prefix look-alikes; the boundary
dir itself counts as in-fence. A missing ownership file fails LOUD at import
(the compliance wrapper turns that into exit 2 + guidance).
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import fs_guard  # noqa: E402
from fs_guard import FenceError, assert_under  # noqa: E402


# ---------- allow ----------

class TestAllow:
    def test_allows_under_zone_root(self, tmp_path):
        target = tmp_path / "docs" / "decisions.md"
        out = assert_under(target, "docs", root=tmp_path)
        assert out == target.resolve()

    def test_allows_nested_subdir(self, tmp_path):
        target = tmp_path / "docs" / "deep" / "er" / "x.md"
        assert assert_under(target, "docs", root=tmp_path) == target.resolve()

    def test_allows_the_boundary_dir_itself(self, tmp_path):
        target = tmp_path / "docs"
        assert assert_under(target, "docs", root=tmp_path) == target.resolve()

    def test_relative_path_resolves_against_root(self, tmp_path):
        out = assert_under(Path("docs") / "x.md", "docs", root=tmp_path)
        assert out == (tmp_path / "docs" / "x.md").resolve()


# ---------- block ----------

class TestBlock:
    def test_blocks_absolute_escape(self, tmp_path):
        with pytest.raises(FenceError) as exc:
            assert_under(Path("/tmp/evil.md"), "docs", root=tmp_path)
        msg = str(exc.value)
        assert "evil.md" in msg
        assert "docs" in msg  # names the zone/boundary it refused against

    def test_blocks_dotdot_traversal(self, tmp_path):
        target = tmp_path / "docs" / ".." / "escape.md"
        with pytest.raises(FenceError):
            assert_under(target, "docs", root=tmp_path)

    def test_blocks_sibling_outside_zone(self, tmp_path):
        target = tmp_path / "notes" / "x.md"
        with pytest.raises(FenceError):
            assert_under(target, "docs", root=tmp_path)

    def test_blocks_symlink_escape(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        zone = tmp_path / "docs"
        zone.mkdir()
        link = zone / "link"
        link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(FenceError):
            assert_under(link / "evil.md", "docs", root=tmp_path)

    def test_blocks_prefix_lookalike(self, tmp_path):
        # `docs-extra` shares the string prefix but not the path segments;
        # naive startswith would wrongly allow it.
        target = tmp_path / "docs-extra" / "x.md"
        with pytest.raises(FenceError):
            assert_under(target, "docs", root=tmp_path)

    def test_no_write_on_block(self, tmp_path):
        target = tmp_path / "escape.md"
        with pytest.raises(FenceError):
            assert_under(target, "docs", root=tmp_path)
        assert not target.exists()

    def test_unknown_zone_raises_naming_known_zones(self, tmp_path):
        with pytest.raises(FenceError) as exc:
            assert_under(tmp_path / "x", "no-such-zone", root=tmp_path)
        assert "docs" in str(exc.value)  # lists the known zones


# ---------- data-driven zones from ownership.yaml ----------

@pytest.fixture()
def custom_ownership(tmp_path):
    """Mutate fs_guard against a custom ownership file; restore module after."""
    old_dict = dict(fs_guard.__dict__)

    def _apply(content: str):
        p = tmp_path / "own.yaml"
        p.write_text(content, encoding="utf-8")
        os.environ["HARNESS_OWNERSHIP_FILE"] = str(p)
        # Reload zones in-place without minting a new FenceError class.
        fs_guard.ZONES = fs_guard._load_zones()
        return fs_guard

    yield _apply
    os.environ.pop("HARNESS_OWNERSHIP_FILE", None)
    # Restore the original module dict so later tests keep the same class identity.
    fs_guard.__dict__.clear()
    fs_guard.__dict__.update(old_dict)


class TestDataDrivenZones:
    def test_default_zones_cover_harness_set(self):
        # The committed ownership.yaml declares the harness zone table.
        for zone in ("docs", "state", "standards", "plans"):
            assert zone in fs_guard.ZONES

    def test_multi_root_zone_allows_each_root(self, custom_ownership, tmp_path):
        fg = custom_ownership("zones:\n  wide: [a/, b/sub/]\n")
        assert fg.assert_under(tmp_path / "a" / "x", "wide", root=tmp_path)
        assert fg.assert_under(tmp_path / "b" / "sub" / "y", "wide", root=tmp_path)
        with pytest.raises(fg.FenceError):
            fg.assert_under(tmp_path / "b" / "z", "wide", root=tmp_path)

    def test_missing_ownership_file_fails_loud_at_import(self, custom_ownership, tmp_path):
        os.environ["HARNESS_OWNERSHIP_FILE"] = str(tmp_path / "nope.yaml")
        old_dict = dict(fs_guard.__dict__)
        try:
            with pytest.raises(Exception) as exc:
                importlib.reload(fs_guard)
            assert "ownership" in str(exc.value).lower()
        finally:
            os.environ.pop("HARNESS_OWNERSHIP_FILE", None)
            fs_guard.__dict__.clear()
            fs_guard.__dict__.update(old_dict)

    def test_malformed_ownership_fails_loud_at_import(self, custom_ownership, tmp_path):
        p = tmp_path / "own.yaml"
        p.write_text("zones: [not, a, mapping]\n", encoding="utf-8")
        os.environ["HARNESS_OWNERSHIP_FILE"] = str(p)
        old_dict = dict(fs_guard.__dict__)
        try:
            with pytest.raises(Exception) as exc:
                importlib.reload(fs_guard)
            assert "ownership" in str(exc.value).lower()
        finally:
            os.environ.pop("HARNESS_OWNERSHIP_FILE", None)
            fs_guard.__dict__.clear()
            fs_guard.__dict__.update(old_dict)

    def test_absolute_zone_root_is_rejected_at_use(self, custom_ownership, tmp_path):
        # An absolute root in ownership.yaml would silently widen the fence
        # to anywhere on the filesystem (pathlib drops `base` when joining an
        # absolute path). Containment helper must refuse it, not honor it.
        fg = custom_ownership("zones:\n  evil: [/etc]\n  docs: [docs/]\n")
        with pytest.raises(fg.FenceError) as exc:
            fg.assert_under(Path("/etc/passwd"), "evil", root=tmp_path)
        assert "evil" in str(exc.value)

    def test_dotdot_zone_root_is_rejected_at_use(self, custom_ownership, tmp_path):
        # A `../`-laden root resolves outside the repo root — same silent
        # widening, same refusal.
        fg = custom_ownership(
            "zones:\n  evil: ['../../../../tmp/pwned']\n  docs: [docs/]\n")
        with pytest.raises(fg.FenceError):
            fg.assert_under(Path("/tmp/pwned/x.md"), "evil", root=tmp_path)

    def test_clean_zones_unaffected_by_root_validation(self, custom_ownership, tmp_path):
        fg = custom_ownership("zones:\n  docs: [docs/]\n")
        target = tmp_path / "docs" / "x.md"
        assert fg.assert_under(target, "docs", root=tmp_path) == target.resolve()



def test_whole_tree_zone_root_is_rejected_at_use(custom_ownership, tmp_path):
    # a `.` root resolves to the repo root itself, silently widening the fence to the
    # whole tree (incl .git/) — refuse it as loudly as an out-of-repo root.
    fg = custom_ownership("zones:\n  wide: ['.']\n  docs: [docs/]\n")
    with pytest.raises(fg.FenceError):
        fg.assert_under(Path(".git/hooks/x"), "wide", root=tmp_path)


# ---------- global layout: per-zone root binding (bin != project) ----------

class TestGlobalZoneBinding:
    """Under a global install the project zones (docs/plans) resolve against the
    PROJECT root and the bin zone (standards) against the BIN root — regardless of
    what root a caller passes, so a caller that still hands over bin-root (e.g.
    plan_approval) no longer mis-zones a project write."""

    def _global(self, monkeypatch, tmp_path):
        binr = tmp_path / "bin"; proj = tmp_path / "A"
        for d in (binr / "harness" / "standards", proj / "docs", proj / "plans"):
            d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(binr))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        monkeypatch.delenv("HARNESS_DATA_ROOT", raising=False)
        monkeypatch.delenv("HARNESS_STATE_DIR", raising=False)
        return binr, proj

    def test_docs_zone_resolves_project_root(self, monkeypatch, tmp_path):
        binr, proj = self._global(monkeypatch, tmp_path)
        out = fs_guard.assert_under(proj / "docs" / "x.md", "docs")
        assert out == (proj / "docs" / "x.md").resolve()
        with pytest.raises(fs_guard.FenceError):  # module attr — reload-proof identity
            fs_guard.assert_under(binr / "docs" / "x.md", "docs")

    def test_standards_zone_resolves_bin_root(self, monkeypatch, tmp_path):
        binr, proj = self._global(monkeypatch, tmp_path)
        out = fs_guard.assert_under(binr / "harness" / "standards" / "x", "standards")
        assert out == (binr / "harness" / "standards" / "x").resolve()

    def test_project_zone_defaults_to_project_not_bin(self, monkeypatch, tmp_path):
        binr, proj = self._global(monkeypatch, tmp_path)
        with pytest.raises(fs_guard.FenceError):  # module attr — reload-proof identity
            fs_guard.assert_under(binr / "plans" / "p" / "x", "plans")
        assert fs_guard.assert_under(proj / "plans" / "p" / "x", "plans") == \
            (proj / "plans" / "p" / "x").resolve()

    def test_project_zone_failclosed_when_project_unresolved(self, monkeypatch, tmp_path):
        binr = tmp_path / "bin"; binr.mkdir()
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(binr))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.delenv("HARNESS_DATA_ROOT", raising=False)
        with pytest.raises(fs_guard.FenceError):  # module attr — reload-proof identity
            fs_guard.assert_under(tmp_path / "docs" / "x.md", "docs")
