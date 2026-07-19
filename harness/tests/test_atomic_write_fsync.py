"""Durable-write contract (D5): the load-bearing config / register / snapshot /
stamp / override writers must fsync the tmp before os.replace.

os.replace makes the directory ENTRY atomic, but the tmp's data blocks may still
be unflushed — a crash between the rename and the flush leaves an EMPTY/torn file
(the classic rename-without-fsync gap). For guard-policy.yaml that means the cage
silently loads its default and drops every override; for the DEC register it means
a lost decision. These tests pin the shared primitive plus each of the five sites
that route through it.
"""
import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_io  # noqa: E402


@pytest.fixture
def spy_fsync(monkeypatch):
    """Record every os.fsync fd (the durability barrier). os.fsync is one object
    across every module, so patching it here catches every site's write."""
    calls = []
    real = os.fsync

    def spy(fd):
        calls.append(fd)
        return real(fd)

    monkeypatch.setattr(os, "fsync", spy)
    return calls


class TestPrimitive:
    def test_fsyncs_and_replaces_atomically(self, tmp_path, spy_fsync):
        p = tmp_path / "x.yaml"
        artifact_io.atomic_write_text(p, "a: 1\n")
        assert p.read_text() == "a: 1\n"
        assert spy_fsync, "atomic_write_text must fsync the tmp before replace"
        assert not (tmp_path / "x.yaml.tmp").exists()

    def test_newline_passthrough(self, tmp_path):
        # register writers open with newline="" to keep literal CRLF/LF bytes.
        p = tmp_path / "r.md"
        artifact_io.atomic_write_text(p, "a\r\nb\n", newline="")
        assert p.read_bytes() == b"a\r\nb\n"


class TestSitesFsync:
    def test_register_store_atomic_write(self, tmp_path, spy_fsync):
        import register_store
        p = tmp_path / "reg.md"
        register_store.atomic_write(p, "line\n")
        assert p.read_text() == "line\n"
        assert spy_fsync, "register_store.atomic_write must fsync (DEC/backlog/glossary)"

    def test_explore_override_atomic_write(self, tmp_path, spy_fsync):
        import explore_override
        p = tmp_path / "sub" / "marker.json"
        explore_override._atomic_write(p, {"a": 1})
        assert json.loads(p.read_text()) == {"a": 1}
        assert p.parent.is_dir()  # mkdir(parents) preserved
        assert spy_fsync

    def test_artifact_stamp_stamp_file(self, tmp_path, spy_fsync):
        import artifact_stamp
        p = tmp_path / "doc.md"
        p.write_text("# title\n\nbody\n", encoding="utf-8")
        spy_fsync.clear()  # ignore the plain seed write above
        changed = artifact_stamp.stamp_file(p, "1.0.0", "deadbeefcafe")
        assert changed is True
        assert spy_fsync, "stamp_file must fsync its atomic write"

    def test_verification_snapshot(self, tmp_path, spy_fsync):
        import yaml
        import verification_snapshot
        arts = tmp_path / "artifacts"
        arts.mkdir()
        (arts / "verification.yaml").write_text(
            yaml.safe_dump({"verdict": "PASS", "phase": "1"}), encoding="utf-8")
        spy_fsync.clear()
        verification_snapshot.snapshot(tmp_path)
        out = tmp_path / "artifacts" / "verification-1.json"
        assert out.exists(), "snapshot must write the phase evidence"
        assert spy_fsync

    def test_guard_config_routes_through_primitive(self, tmp_path, monkeypatch):
        import yaml
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
        policy = tmp_path / "guard-policy.yaml"
        policy.write_text("# seed\n" + yaml.safe_dump({"preset": "balanced", "overrides": {}}),
                          encoding="utf-8")
        import guard_config
        seen = {}
        real = artifact_io.atomic_write_text

        def spy(path, body, **k):
            seen["called"] = True
            return real(path, body, **k)

        monkeypatch.setattr(artifact_io, "atomic_write_text", spy)
        guard_config._locked_update(policy, gid="bash_safety_guard", mode="warn")
        assert seen.get("called"), "guard_config must route its write through the fsyncing primitive"
        assert policy.read_text()  # non-empty after the write
