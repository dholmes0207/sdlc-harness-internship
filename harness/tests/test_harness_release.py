"""test_harness_release.py — version identity read + kit-digest compute.

release.json is the harness version identity, SEPARATE from manifest.json (the
pure integrity map). compute_kit_digest fingerprints the whole tree as the
sha256 of manifest.json; read_release returns the file fresh (uncached, so a
mid-session upgrade is reflected), falling back to a dev identity when no
release has been cut and recomputing the digest live on the dev channel.
"""
import hashlib
import json
from pathlib import Path

import pytest

import harness_release  # importable via conftest (scripts/ on sys.path)


def _write_manifest(root: Path, files: dict) -> Path:
    m = root / "harness" / "manifest.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"files": files}, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
    return m


class TestKitDigest:
    def test_digest_is_sha256_of_manifest_file(self, tmp_path):
        m = _write_manifest(tmp_path, {"harness/a.py": "0" * 64})
        expected = hashlib.sha256(m.read_bytes()).hexdigest()
        assert harness_release.compute_kit_digest(tmp_path) == expected
        assert len(expected) == 64

    def test_digest_empty_when_no_manifest(self, tmp_path):
        assert harness_release.compute_kit_digest(tmp_path) == ""

    def test_digest_changes_when_tree_changes(self, tmp_path):
        _write_manifest(tmp_path, {"harness/a.py": "0" * 64})
        d1 = harness_release.compute_kit_digest(tmp_path)
        _write_manifest(tmp_path, {"harness/a.py": "1" * 64})
        d2 = harness_release.compute_kit_digest(tmp_path)
        assert d1 != d2


class TestReadRelease:
    def test_reads_pinned_stable_release(self, tmp_path):
        rel = {"schema_version": "1.0", "harness_version": "1.2.3",
               "kit_digest": "abc123", "channel": "stable"}
        (tmp_path / "harness").mkdir(parents=True)
        (tmp_path / "harness" / "release.json").write_text(
            json.dumps(rel), encoding="utf-8")
        got = harness_release.read_release(tmp_path)
        assert got["harness_version"] == "1.2.3"
        assert got["channel"] == "stable"
        assert got["kit_digest"] == "abc123"  # pinned digest trusted as-is

    def test_fallback_dev_identity_when_absent(self, tmp_path):
        _write_manifest(tmp_path, {"harness/a.py": "0" * 64})
        got = harness_release.read_release(tmp_path)
        assert got["harness_version"] == harness_release.DEV_VERSION
        assert got["channel"] == "dev"
        assert got["kit_digest"] == harness_release.compute_kit_digest(tmp_path)

    def test_fallback_when_malformed(self, tmp_path):
        (tmp_path / "harness").mkdir(parents=True)
        (tmp_path / "harness" / "release.json").write_text(
            "{bad json", encoding="utf-8")
        got = harness_release.read_release(tmp_path)
        assert got["harness_version"] == harness_release.DEV_VERSION

    def test_dev_channel_recomputes_digest_live(self, tmp_path):
        # A committed dev release.json may carry an empty digest; read_release
        # fills it from the working tree so a dev stamp is never stale.
        _write_manifest(tmp_path, {"harness/a.py": "0" * 64})
        (tmp_path / "harness" / "release.json").write_text(
            json.dumps({"harness_version": "0.1.0", "channel": "dev",
                        "kit_digest": ""}), encoding="utf-8")
        got = harness_release.read_release(tmp_path)
        assert got["kit_digest"] == harness_release.compute_kit_digest(tmp_path)
        assert got["kit_digest"] != ""

    def test_fresh_read_reflects_mid_session_change(self, tmp_path):
        # Provenance must reflect an upgrade applied mid-session: read_release is
        # uncached, so a rewrite between calls is seen on the next read.
        h = tmp_path / "harness"
        h.mkdir(parents=True)
        rel = h / "release.json"
        rel.write_text(json.dumps(
            {"harness_version": "1.0.0", "kit_digest": "x", "channel": "stable"}),
            encoding="utf-8")
        assert harness_release.read_release(tmp_path)["harness_version"] == "1.0.0"
        rel.write_text(json.dumps(
            {"harness_version": "1.0.1", "kit_digest": "y", "channel": "stable"}),
            encoding="utf-8")
        assert harness_release.read_release(tmp_path)["harness_version"] == "1.0.1"
