"""Tests for trust_store — the TOFU trust layer gating shell-detector auto-fire.

A shell detector sourced from a rule file is an RCE vector if the rule came from
an untrusted clone. trust_store answers two questions: is this repo root trusted
(the operator ran `hs-cli trust`), and is this rule file base-verified (its bytes
match the shipped manifest digest — NOT merely "lives under harness/", which a
hostile clone could fake). Either answer being yes lets a shell detector fire;
both no drops it to grep-only.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import trust_store  # noqa: E402


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TRUST_STORE", str(tmp_path / "trust.json"))


def test_trust_store_roundtrip(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    repo = tmp_path / "repoA"; repo.mkdir()
    other = tmp_path / "repoB"; other.mkdir()
    assert trust_store.is_trusted(repo) is False
    trust_store.add_trust(repo)
    assert trust_store.is_trusted(repo) is True
    assert trust_store.is_trusted(other) is False
    # idempotent: a second add does not duplicate
    trust_store.add_trust(repo)
    assert len(trust_store.load_trust()) == 1


def test_add_trust_dir_store_raises_trusterror(tmp_path, monkeypatch):
    # a store path that is a directory surfaces as TrustError, not a raw OSError
    store_dir = tmp_path / "store-as-dir"
    store_dir.mkdir()
    monkeypatch.setenv("HARNESS_TRUST_STORE", str(store_dir))
    repo = tmp_path / "repo"; repo.mkdir()
    with pytest.raises(trust_store.TrustError):
        trust_store.add_trust(repo)


def test_symlink_root_rejected(tmp_path, monkeypatch):
    # [F6] a symlinked repo root is refused (a symlink could point the trusted
    # name at hostile content after the fact)
    _store(tmp_path, monkeypatch)
    real = tmp_path / "real"; real.mkdir()
    link = tmp_path / "link"; link.symlink_to(real, target_is_directory=True)
    with pytest.raises(trust_store.TrustError):
        trust_store.add_trust(link)
    assert trust_store.is_trusted(link) is False


def test_realpath_traversal_normalized(tmp_path, monkeypatch):
    # [F6] realpath normalizes `..` so an equivalent path matches, a different
    # one does not (no false match via traversal)
    _store(tmp_path, monkeypatch)
    repo = tmp_path / "repoA"; repo.mkdir()
    (tmp_path / "nope").mkdir()
    trust_store.add_trust(repo)
    assert trust_store.is_trusted(tmp_path / "repoA" / ".." / "repoA") is True
    assert trust_store.is_trusted(tmp_path / "repoA" / ".." / "nope") is False


def test_base_verified_matches_manifest(tmp_path):
    # [F4] base-verified = bytes match the manifest digest, NOT path-under-harness
    root = tmp_path
    (root / "harness").mkdir()
    f = root / "harness" / "x.std.yaml"
    f.write_text("content A", encoding="utf-8")
    dig = hashlib.sha256(b"content A").hexdigest()
    (root / "harness" / "manifest.json").write_text(
        json.dumps({"files": {"harness/x.std.yaml": dig}}), encoding="utf-8")
    assert trust_store.is_base_verified(f, root) is True
    # mutate -> digest mismatch -> NOT base-verified even though still under harness/
    f.write_text("content B", encoding="utf-8")
    assert trust_store.is_base_verified(f, root) is False
    # a file absent from the manifest is not base-verified
    g = root / "harness" / "y.std.yaml"; g.write_text("z", encoding="utf-8")
    assert trust_store.is_base_verified(g, root) is False
    # a file outside the root is not base-verified
    outside = tmp_path.parent / "elsewhere.yaml"
    assert trust_store.is_base_verified(outside, root) is False


def test_symlink_rule_file_not_base_verified(tmp_path):
    # a symlinked rule file is refused even when it points at an intact,
    # manifest-listed target (a symlink could inherit a base file's verification)
    root = tmp_path
    (root / "harness").mkdir()
    real = root / "harness" / "real.std.yaml"
    real.write_text("content A", encoding="utf-8")
    import hashlib
    import json
    (root / "harness" / "manifest.json").write_text(
        json.dumps({"files": {"harness/real.std.yaml": hashlib.sha256(b"content A").hexdigest(),
                              "harness/link.std.yaml": hashlib.sha256(b"content A").hexdigest()}}),
        encoding="utf-8")
    link = root / "harness" / "link.std.yaml"
    link.symlink_to(real)
    assert trust_store.is_base_verified(real, root) is True
    assert trust_store.is_base_verified(link, root) is False   # symlink refused
