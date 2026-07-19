"""test_install_convergence.py — install/verify convergence + help/dry-run.

1. Orphan report: a harness/ file present on disk but ABSENT from the manifest is
   reported as a WARN-class problem by verify (it does not fail --strict by
   itself); the install --prune flag removes such an outside-manifest file.
2. Hash-checked pre-push restore: uninstall restores pre-push.bak ONLY when the
   current pre-push is the harness hook. A user-installed foreign hook present at
   uninstall is left alone (no restore-clobber).
3. The --components help text reflects the spine-only default (not 'all').
4. --dry-run previews the plugin marketplace + enabledPlugins wiring by sourcing
   the marketplace from the SOURCE tree (the target is never copied on dry-run).
"""
import json
import sys
from pathlib import Path

import pytest  # noqa: F401

_HERE = Path(__file__).resolve().parent
_INSTALL = _HERE.parent / "install"
_SCRIPTS = _HERE.parent / "scripts"
for _p in (str(_INSTALL), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import install  # noqa: E402
import verify_install  # noqa: E402


def _seed_manifest(root, files):
    """Write a harness/manifest.json hashing exactly `files` (rel -> content) so
    verify treats them as the tracked set; create them on disk too."""
    from build_manifest import sha256_file
    hashes = {}
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        hashes[rel] = sha256_file(p)
    mpath = root / "harness" / "manifest.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps({"files": hashes}), encoding="utf-8")


class TestOrphanReport:
    def test_orphan_file_is_reported_warn_class(self, tmp_path):
        """A harness/ file on disk but not in the manifest is reported as an
        orphan, and the orphan report is NOT part of the hard (strict-failing)
        problem set."""
        root = tmp_path / "repo"
        _seed_manifest(root, {"harness/tracked.py": "print('ok')\n"})
        orphan = root / "harness" / "scripts" / "leftover.py"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("# orphan\n", encoding="utf-8")

        orphans = verify_install.orphan_problems(root)
        rels = {rel for rel, _ in orphans}
        assert "harness/scripts/leftover.py" in rels, orphans

        # the manifest-tracked file (clean) and the manifest itself are not orphans
        assert "harness/tracked.py" not in rels
        assert "harness/manifest.json" not in rels

        # orphans never enter the integrity (strict) problem set
        hard = verify_install.verify(root)
        hard_rels = {rel for rel, _ in hard}
        assert "harness/scripts/leftover.py" not in hard_rels

    def test_no_orphans_when_disk_matches_manifest(self, tmp_path):
        root = tmp_path / "repo"
        _seed_manifest(root, {"harness/tracked.py": "print('ok')\n"})
        assert verify_install.orphan_problems(root) == []

    def test_prune_removes_outside_manifest_harness_file(self, tmp_path):
        """install --prune deletes a harness/ file that the manifest does not
        cover; a manifest-tracked file is untouched."""
        root = tmp_path / "repo"
        _seed_manifest(root, {"harness/tracked.py": "print('ok')\n"})
        orphan = root / "harness" / "scripts" / "leftover.py"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("# orphan\n", encoding="utf-8")

        result = {"actions": [], "warnings": []}
        install._prune_orphans(root, result, dry_run=False)

        assert not orphan.exists(), "orphan was not pruned"
        assert (root / "harness" / "tracked.py").exists()

    def test_prune_dry_run_keeps_the_file(self, tmp_path):
        root = tmp_path / "repo"
        _seed_manifest(root, {"harness/tracked.py": "print('ok')\n"})
        orphan = root / "harness" / "scripts" / "leftover.py"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("# orphan\n", encoding="utf-8")

        result = {"actions": [], "warnings": []}
        install._prune_orphans(root, result, dry_run=True)

        assert orphan.exists(), "dry-run prune must not delete"


class TestUninstallPrepushIdentityChecked:
    def _gate_src(self, source):
        gate = source / "harness" / "install" / "git-pre-push-hook.sh"
        gate.parent.mkdir(parents=True, exist_ok=True)
        # carries the stable header marker _uninstall_prepush identifies the
        # harness hook by (every version's hook carries it — _prepush._HOOK_MARKER)
        gate.write_text(
            "#!/bin/sh\n# git-pre-push-hook.sh — transport-level stage gate\n"
            "# harness transport gate\n")
        return gate

    def test_foreign_current_hook_is_not_restore_clobbered(self, tmp_path):
        """At uninstall the current pre-push is a user-installed FOREIGN hook (not
        the harness hook). The harness must not restore its .bak over it — the
        user's live hook stays exactly as-is and the .bak is left in place."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        self._gate_src(source)
        hooks = target / ".git" / "hooks"
        hooks.mkdir(parents=True)
        current = "#!/bin/sh\n# user's own hook installed AFTER the harness\n"
        (hooks / "pre-push").write_text(current)
        bak_content = "#!/bin/sh\n# harness-saved foreign backup\n"
        (hooks / "pre-push.bak").write_text(bak_content)

        result = {"actions": [], "warnings": []}
        install._uninstall_prepush(source, target, result, dry_run=False)

        # current hook is NOT clobbered by a restore
        assert (hooks / "pre-push").read_text() == current
        # the .bak is preserved (no unconditional consume)
        assert (hooks / "pre-push.bak").read_text() == bak_content

    def test_harness_hook_is_restored_from_bak(self, tmp_path):
        """When the current pre-push IS the harness hook, uninstall restores the
        .bak (the foreign hook the install backed up) and consumes it."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        gate = self._gate_src(source)
        hooks = target / ".git" / "hooks"
        hooks.mkdir(parents=True)
        # current == the harness hook
        (hooks / "pre-push").write_text(gate.read_text())
        restored = "#!/bin/sh\n# the original foreign hook\n"
        (hooks / "pre-push.bak").write_text(restored)

        result = {"actions": [], "warnings": []}
        install._uninstall_prepush(source, target, result, dry_run=False)

        assert (hooks / "pre-push").read_text() == restored
        assert not (hooks / "pre-push.bak").exists()

    def test_harness_hook_no_bak_is_removed(self, tmp_path):
        """Harness hook present, no .bak → remove the harness hook outright."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        gate = self._gate_src(source)
        hooks = target / ".git" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "pre-push").write_text(gate.read_text())

        result = {"actions": [], "warnings": []}
        install._uninstall_prepush(source, target, result, dry_run=False)

        assert not (hooks / "pre-push").exists()


class TestComponentsHelpDefault:
    def test_components_help_says_spine_only_not_all_default(self):
        help_text = _components_help()
        low = help_text.lower()
        # the corrected text must NOT claim 'all' is the default
        assert "'all' (default)" not in low, help_text
        assert "default: all" not in low, help_text
        assert "default 'all'" not in low, help_text
        # it should point at the spine-only default instead
        assert "spine" in low, help_text


def _components_help() -> str:
    """Pull the --components help string from the live argparse parser main()
    builds, without running the install."""
    import argparse
    holder = {}
    real_parse = argparse.ArgumentParser.parse_args

    def _capture(self, *a, **k):
        for action in self._actions:
            if "--components" in (action.option_strings or []):
                holder["help"] = action.help
        raise SystemExit(0)  # stop before any install work

    argparse.ArgumentParser.parse_args = _capture
    try:
        try:
            install.main(["--components", "all"])
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = real_parse
    return holder.get("help", "")


class TestDryRunPluginPreview:
    def _source_with_marketplace(self, root, plugins):
        mpdir = root / "harness" / "plugins" / ".claude-plugin"
        mpdir.mkdir(parents=True)
        mp = {"name": "hs-local",
              "plugins": [{"name": n, "source": "./%s" % n} for n in plugins]}
        (mpdir / "marketplace.json").write_text(json.dumps(mp), encoding="utf-8")

    def test_dry_run_lists_marketplace_plugins_from_source(self, tmp_path):
        """On dry-run the target tree is NOT copied, so the marketplace must be
        read from the SOURCE so the preview lists what WOULD be wired."""
        source = tmp_path / "source"
        target = tmp_path / "target"  # empty — never copied on dry-run
        target.mkdir()
        self._source_with_marketplace(source, ["hs", "hs-extra", "hs-viz"])

        result = {"actions": [], "warnings": [], "problems": []}
        install._wire_plugins(target, "all", True, result, dry_run=True,
                              source_root=source)

        joined = " ".join(result["actions"])
        assert "hs" in joined
        assert "hs-extra" in joined
        assert "hs-viz" in joined
        # nothing was written on dry-run
        assert not (target / ".claude").exists()
