"""hs:spec vendored front-end libs — third-party NOTICE + SHA integrity pin.

The visualize HTML views ship three vendored libraries inline (offline, no CDN):
Mermaid, marked, DOMPurify. The phase-4 plan required them "pinned + SHA-verified
committed" with a test verifying the SHA — this locks that contract:

  1. every vendored `*.min.js` matches its pinned sha256 (a silent swap / corrupt
     re-vendor is caught, matching the drawio VIEWER pin precedent), and
  2. the NOTICE records each lib's version + license + the same pinned SHA
     (third-party attribution, mirroring harness/plugins/hs/skills/drawio/NOTICE).

Bump BOTH the PINS dict here and the NOTICE together when re-vendoring upstream.
"""
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR = REPO_ROOT / "harness/plugins/hs/skills/spec/assets/vendor"
NOTICE = VENDOR / "NOTICE"

# (filename, upstream version, sha256) — the pinned integrity triple. Read from
# each bundle's own header at vendor time; re-verify before bumping.
PINS = {
    "mermaid.min.js": ("11.4.1", "a43bc1afd446f9c4cc66ac5dd45d02e8d65e26fc5344ec0ef787f88d6ddb6f9e"),
    "marked.min.js": ("18.0.4", "5d35f05a51554f8665066455535e3adf642df0da7e2a18d39766d5a3ecb4846c"),
    "purify.min.js": ("3.4.7", "f84e522876a6cfadecb89c173356409acec39f580c69018559c9a50e96299b0c"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_vendored_files_match_pinned_sha256():
    for name, (_version, sha) in PINS.items():
        f = VENDOR / name
        assert f.is_file(), f"vendored lib missing: {name}"
        actual = _sha256(f)
        assert actual == sha, (
            f"{name} sha256 drift: {actual} != pinned {sha} — a re-vendor or "
            f"corruption changed the shipped bundle; re-verify upstream and bump "
            f"the PIN + NOTICE together"
        )


def test_notice_records_each_lib_version_and_sha():
    assert NOTICE.is_file(), "spec vendor NOTICE missing (third-party attribution)"
    text = NOTICE.read_text(encoding="utf-8")
    for name, (version, sha) in PINS.items():
        assert version in text, f"NOTICE missing {name} version {version}"
        assert sha in text, f"NOTICE missing {name} pinned sha256 {sha}"


def test_notice_names_each_license():
    text = NOTICE.read_text(encoding="utf-8")
    # Mermaid + marked are MIT; DOMPurify ships dual Apache-2.0 / MPL-2.0.
    assert "MIT" in text
    assert "Apache" in text and "2.0" in text
    assert "MPL" in text or "Mozilla Public License" in text
