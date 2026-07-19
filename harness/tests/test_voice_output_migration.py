"""test_voice_output_migration.py — migration script tests.

Tests for migrate_voice_output.py: rewrite old terminal-voice.yaml fields
(output_style, detail_level) into the new schema.
"""

import sys
import tempfile
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DOCS = Path(__file__).resolve().parents[2] / "docs"

sys.path.insert(0, str(SCRIPTS))

MIGRATE_SCRIPT = SCRIPTS / "migrate_voice_output.py"
GLOSSARY_MD = DOCS / "GLOSSARY.md"


def _write_old_tv_yaml(path: Path, *, output_style=None, detail_level=None, voice_level=5):
    """Write an old-format terminal-voice.yaml with legacy fields."""
    lines = ["voice_level: %d" % voice_level,
             "persona: none",
             "no_markdown: false",
             "interview_rigor: standard",
             "action_prompting: standard"]
    if output_style is not None:
        lines.append("output_style: %d" % output_style)
    if detail_level is not None:
        lines.append("detail_level: %s" % detail_level)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_old_out_yaml(path: Path):
    """Write a minimal output.yaml (no code_style yet)."""
    path.write_text("language: vi\nhumanize: true\n", encoding="utf-8")


def test_migration_rewrites_both_yaml():
    """--apply on old yaml: output.yaml gets code_style from output_style,
    terminal-voice.yaml loses output_style/detail_level, tvl reflects detail_level."""
    import migrate_voice_output as mig

    with tempfile.TemporaryDirectory() as d:
        tv = Path(d) / "terminal-voice.yaml"
        out = Path(d) / "output.yaml"

        _write_old_tv_yaml(tv, output_style=3, detail_level="verbose")
        _write_old_out_yaml(out)

        mig.migrate(tv_path=str(tv), out_path=str(out), apply=True)

        # output.yaml must have code_style = 3 (from old output_style)
        out_data = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert out_data.get("code_style") == 3, (
            "Expected code_style=3 in output.yaml, got: %r" % out_data)

        # terminal-voice.yaml must not have output_style or detail_level
        tv_data = yaml.safe_load(tv.read_text(encoding="utf-8"))
        assert "output_style" not in tv_data, "output_style still in terminal-voice.yaml"
        assert "detail_level" not in tv_data, "detail_level still in terminal-voice.yaml"

        # terminal_voice_level should reflect verbose=5
        assert tv_data.get("terminal_voice_level") == 5, (
            "Expected terminal_voice_level=5 for detail_level=verbose, got: %r"
            % tv_data.get("terminal_voice_level"))


def test_migration_dryrun_no_write():
    """Dry-run (default) must NOT modify either file; must print diff."""
    import migrate_voice_output as mig

    with tempfile.TemporaryDirectory() as d:
        tv = Path(d) / "terminal-voice.yaml"
        out = Path(d) / "output.yaml"

        _write_old_tv_yaml(tv, output_style=2, detail_level="concise")
        _write_old_out_yaml(out)

        tv_before = tv.read_text(encoding="utf-8")
        out_before = out.read_text(encoding="utf-8")

        # dry-run (apply=False)
        import io, sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            mig.migrate(tv_path=str(tv), out_path=str(out), apply=False)
        finally:
            sys.stdout = old_stdout

        # Files must be unchanged
        assert tv.read_text(encoding="utf-8") == tv_before, "tv_yaml was modified during dry-run"
        assert out.read_text(encoding="utf-8") == out_before, "out_yaml was modified during dry-run"

        # Diff output must mention the changes
        diff_output = captured.getvalue()
        assert "code_style" in diff_output or "output_style" in diff_output or "detail_level" in diff_output, (
            "Dry-run output did not mention expected changes. Got:\n%s" % diff_output
        )


def test_migration_idempotent():
    """Running migration twice on an already-migrated file is a no-op."""
    import migrate_voice_output as mig

    with tempfile.TemporaryDirectory() as d:
        tv = Path(d) / "terminal-voice.yaml"
        out = Path(d) / "output.yaml"

        # Write a CLEAN (already migrated) file
        tv.write_text(
            "voice_level: 5\npersona: none\nterminal_voice_level: 3\n"
            "no_markdown: false\ninterview_rigor: standard\naction_prompting: standard\n",
            encoding="utf-8")
        out.write_text("language: vi\nhumanize: true\ncode_style: 2\n", encoding="utf-8")

        tv_before = tv.read_text(encoding="utf-8")
        out_before = out.read_text(encoding="utf-8")

        mig.migrate(tv_path=str(tv), out_path=str(out), apply=True)

        assert tv.read_text(encoding="utf-8") == tv_before, "tv_yaml changed on second run (not idempotent)"
        assert out.read_text(encoding="utf-8") == out_before, "out_yaml changed on second run (not idempotent)"


def test_migrate_dryrun_flag_detects_legacy():
    """`--dry-run` (explicit) on a legacy tv file reports planned changes, writes
    nothing, exits 0. `--check` exits 1 on legacy, 0 when already clean (idempotent)."""
    import subprocess

    with tempfile.TemporaryDirectory() as d:
        tv = Path(d) / "terminal-voice.yaml"
        out = Path(d) / "output.yaml"
        _write_old_tv_yaml(tv, output_style=3, detail_level="verbose")
        out.write_text("language: vi\n", encoding="utf-8")

        before = tv.read_text(encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(MIGRATE_SCRIPT), "--dry-run",
             "--tv", str(tv), "--out", str(out)],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert ("planned changes" in r.stdout or "code_style" in r.stdout), r.stdout
        assert tv.read_text(encoding="utf-8") == before, "--dry-run wrote to the file"

        # --check on legacy -> non-zero (surfacing signal)
        rc = subprocess.run(
            [sys.executable, str(MIGRATE_SCRIPT), "--check",
             "--tv", str(tv), "--out", str(out)],
            capture_output=True, text=True)
        assert rc.returncode == 1, (
            "--check must exit 1 when legacy keys present; got %d\n%s"
            % (rc.returncode, rc.stdout))

        # --check on a CLEAN config -> exit 0 (idempotent no-op)
        tv.write_text("voice_level: 5\nterminal_voice_level: 3\n", encoding="utf-8")
        out.write_text("language: vi\ncode_style: 3\n", encoding="utf-8")
        rc2 = subprocess.run(
            [sys.executable, str(MIGRATE_SCRIPT), "--check",
             "--tv", str(tv), "--out", str(out)],
            capture_output=True, text=True)
        assert rc2.returncode == 0, (
            "--check must exit 0 on a clean config; got %d\n%s"
            % (rc2.returncode, rc2.stdout))
