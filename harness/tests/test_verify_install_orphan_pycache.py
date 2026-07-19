"""orphan_problems must ignore noise, still surface real orphans.

Excluded as noise: Python bytecode cache (__pycache__/*.pyc), runtime logs
(*/.logs/*, e2e RUN-LOG.md), and release.json (shipped + git-tracked but, like
manifest.json, excluded from the manifest hash map). A genuinely stale
.py left by a prior install must still surface.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import verify_install  # noqa: E402


def _mk(root: Path, tracked: dict, disk: dict) -> None:
    (root / "harness").mkdir(parents=True, exist_ok=True)
    (root / "harness" / "manifest.json").write_text(
        json.dumps({"files": tracked}), encoding="utf-8")
    for rel, content in disk.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)


def test_pycache_not_reported_as_orphan(tmp_path):
    _mk(tmp_path, tracked={"harness/foo.py": "x" * 64},
        disk={"harness/foo.py": b"code",
              "harness/tests/__pycache__/foo.cpython-312.pyc": b"bc",
              "harness/scripts/__pycache__/bar.pyc": b"bc"})
    reported = {rel for rel, _ in verify_install.orphan_problems(tmp_path)}
    assert not any("__pycache__" in r or r.endswith((".pyc", ".pyo"))
                   for r in reported), reported


def test_runtime_logs_not_orphan(tmp_path):
    _mk(tmp_path, tracked={"harness/foo.py": "x" * 64},
        disk={"harness/foo.py": b"code",
              "harness/hooks/.logs/hook-crashes.log": b"trace",
              "harness/e2e/RUN-LOG.md": b"run output"})
    reported = {rel for rel, _ in verify_install.orphan_problems(tmp_path)}
    assert "harness/hooks/.logs/hook-crashes.log" not in reported
    assert "harness/e2e/RUN-LOG.md" not in reported


def test_release_json_not_orphan(tmp_path):
    # release.json ships and is git-tracked, but is excluded from the manifest
    # hash map (like manifest.json) — must not read as an orphan.
    _mk(tmp_path, tracked={"harness/foo.py": "x" * 64},
        disk={"harness/foo.py": b"code",
              "harness/release.json": b'{"harness_version": "0"}'})
    reported = {rel for rel, _ in verify_install.orphan_problems(tmp_path)}
    assert "harness/release.json" not in reported


def test_real_orphan_still_reported(tmp_path):
    _mk(tmp_path, tracked={"harness/foo.py": "x" * 64},
        disk={"harness/foo.py": b"code",
              "harness/scripts/stale.py": b"leftover"})
    reported = {rel for rel, _ in verify_install.orphan_problems(tmp_path)}
    assert "harness/scripts/stale.py" in reported


def test_runtime_disabled_skill_stash_not_orphan(tmp_path):
    # A skill disabled at runtime is moved into harness/plugins/hs/disabled-skills/
    # (a tracked sibling of skills/, but the MOVED dir is not in the shipped manifest).
    # Like harness/state/, this stash is runtime-mutable and must not read as an orphan
    # — otherwise `install --prune` would delete the very skill the user parked.
    _mk(tmp_path, tracked={"harness/foo.py": "x" * 64},
        disk={"harness/foo.py": b"code",
              "harness/plugins/hs/disabled-skills/excalidraw/SKILL.md": b"---\nname: hs:excalidraw\n---\n",
              "harness/plugins/hs/disabled-skills/excalidraw/references/x.md": b"body"})
    reported = {rel for rel, _ in verify_install.orphan_problems(tmp_path)}
    assert not any("disabled-skills/" in r for r in reported), reported
