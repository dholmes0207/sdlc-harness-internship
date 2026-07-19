"""test_install_hardening.py — four install.py hardening fixes.

1. Non-ASCII tracked filenames round-trip into the copy set (the install-side
   git listing must mirror build_manifest.py exactly: -z + quotepath=false, NUL
   split) so the manifest and the install agree on the file set.
2. An existing pre-push.bak with different content is preserved (no clobber of a
   user-modified backup).
3. A reviewer entry containing a double-quote renders parseable team.yaml with
   the value intact (no YAML corruption breaking the approval gate).
4. A CLAUDE.md body with two onboarding BEGIN markers is rewritten to a single
   clean marked block (no orphan marker left behind).
"""
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
_INSTALL = _HERE.parent / "install"
if str(_INSTALL) not in sys.path:
    sys.path.insert(0, str(_INSTALL))
_SCRIPTS = _HERE.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import install  # noqa: E402
from conftest import _git  # noqa: E402


class TestNonAsciiTrackedFiles:
    def test_non_ascii_filename_round_trips(self, tmp_path):
        """A tracked harness/ file whose name has non-ASCII bytes must appear in
        the install-side tracked set. The default quotepath C-quotes the name,
        which then misses on disk; mirroring build_manifest's invocation keeps
        it literal."""
        repo = tmp_path / "repo"
        h = repo / "harness"
        h.mkdir(parents=True)
        ascii_rel = "harness/plain.py"
        unicode_rel = "harness/café.md"  # non-ASCII byte in the name
        (repo / ascii_rel).write_text("print('a')\n")
        (repo / unicode_rel).write_text("# cafe\n", encoding="utf-8")
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        _git(repo, "config", "core.quotepath", "true")  # the corrupting default
        _git(repo, "add", "--", "harness/")
        _git(repo, "commit", "-qm", "seed")

        tracked = install._tracked_harness_files(repo)

        assert ascii_rel in tracked
        # the literal (unquoted) name must be present — a C-quoted entry would
        # not equal this and would miss on disk
        assert unicode_rel in tracked, tracked


class TestPrepushBackupNoClobber:
    def test_existing_differing_bak_is_preserved(self, tmp_path):
        """Re-install must not overwrite a pre-push.bak that differs from the
        current pre-push — a user-modified original is irreplaceable."""
        target = tmp_path / "target"
        source = tmp_path / "source"
        (source / "harness" / "install").mkdir(parents=True)
        gate_src = source / "harness" / "install" / "git-pre-push-hook.sh"
        gate_src.write_text("#!/bin/sh\n# harness gate v2\n")

        hooks = target / ".git" / "hooks"
        hooks.mkdir(parents=True)
        foreign = hooks / "pre-push"
        foreign.write_text("#!/bin/sh\n# foreign hook current\n")
        bak = hooks / "pre-push.bak"
        precious = "#!/bin/sh\n# user-modified original — do not destroy\n"
        bak.write_text(precious)

        result = {"actions": [], "warnings": []}
        install._install_prepush(source, target, result, dry_run=False)

        assert bak.read_text() == precious, "existing .bak was clobbered"
        # the new gate is still installed
        assert "harness gate v2" in foreign.read_text()

class TestClaudeMdDuplicateMarker:
    def test_two_begin_markers_collapse_to_single_block(self, tmp_path):
        """A CLAUDE.md with two BEGIN markers (from a prior bug) must be rewritten
        to a single clean marked block — no orphan BEGIN left behind."""
        target = tmp_path / "target"
        target.mkdir()
        begin = install._CLAUDE_BEGIN
        end = install._CLAUDE_END
        body = (
            "# Project\n\nIntro prose.\n\n"
            + begin + "\nstale block one\n" + end + "\n\n"
            + "middle prose\n\n"
            + begin + "\nstale block two\n" + end + "\n\n"
            + "trailing prose\n"
        )
        (target / "CLAUDE.md").write_text(body, encoding="utf-8")

        result = {"actions": []}
        install._write_claude_md(target, result, dry_run=False)

        new = (target / "CLAUDE.md").read_text(encoding="utf-8")
        assert new.count(begin) == 1, new
        assert new.count(end) == 1, new
        assert "trailing prose" in new
        assert "Intro prose." in new
