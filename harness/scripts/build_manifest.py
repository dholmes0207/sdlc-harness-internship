#!/usr/bin/env python3
"""build_manifest.py — write harness/manifest.json: sha256 per file.

Coverage rule: hash ONLY git-tracked files under harness/ — generated
state, RUN-LOG and untracked junk must not poison verify --strict — and never
manifest.json itself (a self-hash is unstable by construction).

Usage:
    python3 harness/scripts/build_manifest.py [--root <repo-root>]
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

MANIFEST_REL = "harness/manifest.json"
# release.json is the version identity (version + channel + kit_digest), kept
# SEPARATE from the pure integrity manifest: hashing it here would make
# the manifest churn on every version bump AND be circular — release.json holds
# kit_digest = sha256(manifest.json). Excluded like manifest.json's self-hash.
RELEASE_REL = "harness/release.json"


def tracked_harness_files(root: Path) -> list:
    """Git-tracked paths under harness/, relative to repo root.

    `-z` (NUL-delimited) + core.quotepath=false keep non-ASCII filenames
    literal: under the default quotepath, git C-quotes such a name
    (``"harness/caf\\303\\251.md"``), which then misses on disk and is silently
    dropped from the manifest — leaving drift on it undetectable.
    """
    out = subprocess.run(
        ["git", "-C", str(root), "-c", "core.quotepath=false",
         "ls-files", "-z", "--", "harness/"],
        capture_output=True, text=True, check=True,
    )
    return [l for l in out.stdout.split("\0")
            if l.strip() and l not in (MANIFEST_REL, RELEASE_REL)]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build(root: Path) -> dict:
    files = {}
    for rel in sorted(tracked_harness_files(root)):
        p = root / rel
        if p.is_file():  # tracked-but-deleted paths are verify's job, not ours
            files[rel] = sha256_file(p)
    # No wall-clock field: the manifest is a content-integrity map, so an
    # unchanged tree must rebuild to byte-identical output (no diff churn).
    return {"files": files}


def serialize_manifest(manifest: dict) -> str:
    """Canonical on-disk manifest text — the ONE place the byte format lives.
    pack.py re-serializes the scrubbed-team manifest and MUST match this exactly,
    or a fresh bundle extract drifts against its own manifest. indent=2 +
    sort_keys + trailing newline = stable, diff-free rebuilds."""
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    manifest = build(root)
    out = root / MANIFEST_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(serialize_manifest(manifest), encoding="utf-8")
    print("manifest: %d files -> %s" % (len(manifest["files"]), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
