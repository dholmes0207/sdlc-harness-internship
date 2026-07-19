"""test_plugin_no_disabled_skill_leak.py — plugin.json must never surface disabled-skills/.

`disabled-skills/` is the OFF-skill stash SSOT (`harness/scripts/disabled_skills.py:52`
`_STASH_REL`). CC only scans a plugin's default `skills/` dir plus any path an explicit
manifest `skills` array adds (findings-batch3-plugin-packaging.md §B3) — today nothing
adds one, so the leak is closed by omission, not by a guard. This test is the guard: it
fails loudly if a future edit ever points a manifest field at the stash (e.g. someone
adds `"skills": ["./disabled-skills/"]` to widen the scan, or renames the stash dir to
something `skills`-prefixed and forgets to update the manifest override), and pins the
real committed plugin.json as clean today.
"""
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_JSON = _REPO_ROOT / "harness" / "plugins" / "hs" / ".claude-plugin" / "plugin.json"

_LEAK_TOKEN = "disabled-skills"


def _find_leaks(manifest):
    """Walk every string value in the manifest (any depth); flag ones containing
    the stash dir name. A manifest is JSON — only str/dict/list/scalar nodes."""
    leaks = []

    def _walk(node, path):
        if isinstance(node, str):
            if _LEAK_TOKEN in node:
                leaks.append((path, node))
        elif isinstance(node, dict):
            for k, v in node.items():
                _walk(v, "%s.%s" % (path, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, "%s[%d]" % (path, i))

    _walk(manifest, "$")
    return leaks


def test_guard_catches_leaked_skills_path():
    fixture = {
        "name": "hs",
        "skills": ["./disabled-skills/x"],
    }
    leaks = _find_leaks(fixture)
    assert leaks, "guard must flag a manifest that adds a disabled-skills/ scan path"
    assert leaks[0][0] == "$.skills[0]"


def test_guard_clean_manifest_has_no_leaks():
    fixture = {
        "name": "hs",
        "description": "clean plugin, no stash reference",
        "keywords": ["sdlc-harness"],
    }
    assert _find_leaks(fixture) == []


def test_committed_plugin_json_has_no_disabled_skills_leak():
    manifest = json.loads(_PLUGIN_JSON.read_text(encoding="utf-8"))
    leaks = _find_leaks(manifest)
    assert leaks == [], (
        "harness/plugins/hs/.claude-plugin/plugin.json declares a path into "
        "disabled-skills/ — this reopens the OFF-skill scan the stash relies on "
        "being invisible to: %r" % leaks
    )


def test_committed_plugin_json_declares_no_skills_override():
    # today the plugin relies on CC's DEFAULT skills/ scan (no manifest override at
    # all) — the cleanest way to guarantee nothing ever points at the stash. If a
    # future change adds an explicit "skills" key, it must not include the stash.
    manifest = json.loads(_PLUGIN_JSON.read_text(encoding="utf-8"))
    if "skills" in manifest:
        for entry in manifest["skills"]:
            assert _LEAK_TOKEN not in entry, (
                "explicit 'skills' override in plugin.json must never point at "
                "disabled-skills/: %r" % entry
            )
