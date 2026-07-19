"""test_bootstrap.py — first-run seed of a project's private .harness/ data home.

A global install ships ONE shared binary; each project keeps its own writeable
`.harness/` skeleton. That skeleton is gitignored, so it must be created the
first time the toolkit runs in a project. `bootstrap.ensure_skeleton` is the
ONLY sanctioned mkdir for the data home: it is idempotent (never clobbers), it
writes STRICTLY under the given data root, and the read-path resolvers in
harness_paths stay PURE (readers never create what they inspect).
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "harness" / "install"))
sys.path.insert(0, str(_REPO_ROOT / "harness" / "scripts"))

import bootstrap  # noqa: E402
import harness_paths  # noqa: E402


class TestEnsureSkeleton:
    def test_creates_skeleton_idempotent(self, tmp_path):
        data = tmp_path / ".harness"
        created = bootstrap.ensure_skeleton(data)
        # first run makes the state tree
        assert (data / "state").is_dir()
        assert (data / "state" / "trace").is_dir()
        assert (data / "state" / "telemetry").is_dir()
        assert created, "first run should report created paths"
        # second run no-ops: nothing new created, existing content untouched
        marker = data / "state" / "telemetry" / "keep.jsonl"
        marker.write_text("x", encoding="utf-8")
        again = bootstrap.ensure_skeleton(data)
        assert again == [], "second run must be a clean no-op"
        assert marker.read_text(encoding="utf-8") == "x", "must not clobber"

    def test_bootstrap_writes_only_under_data_root(self, tmp_path):
        data = tmp_path / "proj" / ".harness"
        bootstrap.ensure_skeleton(data)
        # every created path is contained under data_root; nothing escaped
        outside = tmp_path / "proj"
        for child in outside.rglob("*"):
            assert data.resolve() in child.resolve().parents or child.resolve() == data.resolve(), \
                "bootstrap wrote outside .harness/: %s" % child

    def test_refuses_path_escaping_data_root(self, tmp_path, monkeypatch):
        # a mis-resolved skeleton member that climbs out of data_root is refused,
        # not silently written to the parent.
        data = tmp_path / ".harness"
        monkeypatch.setattr(bootstrap, "_SKELETON_DIRS", ("../escape",))
        try:
            bootstrap.ensure_skeleton(data)
            assert False, "expected containment failure"
        except ValueError:
            pass
        assert not (tmp_path / "escape").exists()

    def test_refuses_unresolved_marker(self, tmp_path):
        # the fail-closed data_root marker must NEVER be materialized on disk.
        try:
            bootstrap.ensure_skeleton(harness_paths._UNRESOLVED)
            assert False, "expected refusal of the unresolved marker"
        except ValueError:
            pass
        assert not harness_paths._UNRESOLVED.exists()

    def test_dry_run_creates_nothing(self, tmp_path):
        data = tmp_path / ".harness"
        planned = bootstrap.ensure_skeleton(data, dry_run=True)
        assert planned, "dry-run should still report what WOULD be created"
        assert not data.exists(), "dry-run must not touch the filesystem"


class TestReadersNeverCreate:
    def test_state_dir_does_not_mkdir(self, tmp_path, monkeypatch):
        # harness_paths.state_dir() is a PURE resolver — inspecting it must not
        # create the project skeleton (only bootstrap, a writer, does).
        monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / ".harness"))
        monkeypatch.delenv("HARNESS_STATE_DIR", raising=False)
        resolved = harness_paths.state_dir()
        assert not resolved.exists(), "state_dir() must not create the dir"

    def test_data_root_does_not_mkdir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / ".harness"))
        resolved = harness_paths.data_root()
        assert not resolved.exists(), "data_root() must not create the dir"
