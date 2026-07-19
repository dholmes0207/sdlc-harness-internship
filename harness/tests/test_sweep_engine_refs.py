"""sweep_engine_refs.transform — deterministic run-ref migration.

Locks the transform behavior: fenced, inline-backtick, and mid-sentence refs all
migrate to the env-path form; read-refs (rules/data) are untouched; idempotent.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import sweep_engine_refs as sw  # noqa: E402

# Build the pre-migration literals from fragments so this test file never trips a
# tracked-file grep-guard on the old form. The sweep now migrates the whole
# `python3 harness/` root, so `_SUB` is the sub-path AFTER it (scripts/, plugins/…,
# hooks/) that survives the replacement.
_OLD = "python3 " + "harness/"
_SUB = "scripts/"


def test_fenced_ref_migrates():
    src = "```bash\n%s%sfoo.py --x\n```\n" % (_OLD, _SUB)
    out = sw.transform_text(src)
    assert sw.NEW + _SUB + "foo.py --x" in out
    assert _OLD not in out


def test_inline_backtick_ref_migrates():
    src = "resolve live via `%s%soutput_config.py --resolved` (never hand-read)\n" % (_OLD, _SUB)
    out = sw.transform_text(src)
    assert sw.NEW + _SUB + "output_config.py --resolved" in out
    assert _OLD not in out


def test_mid_sentence_ref_migrates():
    src = "the %s%sbacklog_register.py add command records it.\n" % (_OLD, _SUB)
    out = sw.transform_text(src)
    assert _OLD not in out
    assert sw.NEW + _SUB + "backlog_register.py add" in out


def test_skill_local_and_hooks_refs_migrate():
    # the round-20 fix: run-refs OUTSIDE harness/scripts/ (skill-local scripts +
    # harness/hooks/) must migrate too, not only the scripts/ dir.
    plugin = _OLD + "plugins/hs/skills/test/scripts/affected_tests.py --base main"
    hook = _OLD + "hooks/gate_stage.py"
    out = sw.transform_text(plugin + "\n" + hook + "\n")
    assert sw.NEW + "plugins/hs/skills/test/scripts/affected_tests.py --base main" in out
    assert sw.NEW + "hooks/gate_stage.py" in out
    assert _OLD not in out


def test_pytest_invocation_not_migrated():
    # `python3 -m pytest harness/tests/` is NOT a `python3 harness/` run-ref — the
    # ` -m pytest ` between breaks the substring, so it must be left untouched.
    src = "run `python3 -m pytest harness/tests/ -q` to check\n"
    assert sw.transform_text(src) == src


def test_read_refs_untouched():
    # rules/data read-refs are not shell run-refs — leave them alone.
    src = "see `harness/rules/output-rendering.md` and `harness/data/output.yaml`\n"
    assert sw.transform_text(src) == src


def test_idempotent():
    src = "run %s%sfoo.py\n" % (_OLD, _SUB)
    once = sw.transform_text(src)
    twice = sw.transform_text(once)
    assert once == twice
    assert once.count(sw.NEW) == 1


def test_count_invariant_on_multi_ref():
    src = "%s%sa.py\n%s%sb.py --k\ntext\n%splugins/x/scripts/c.py\n" % (
        _OLD, _SUB, _OLD, _SUB, _OLD)
    out = sw.transform_text(src)
    assert out.count(sw.NEW) == 3
    assert _OLD not in out


def test_sweep_skips_tracked_but_deleted_file(tmp_path):
    """Round-12 #3: git ls-files reports a tracked-but-deleted path; sweep must skip
    it, not raise a raw FileNotFoundError."""
    import subprocess
    root = tmp_path / "repo"
    d = root / "harness" / "plugins" / "hs" / "skills" / "x"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("run `python3 " + "harness/scripts/foo.py`\n")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "s"], check=True)
    (d / "SKILL.md").unlink()  # deleted from disk, still tracked
    res = sw.sweep(root, dry_run=True)  # must not raise
    assert res["files_touched"] == 0
