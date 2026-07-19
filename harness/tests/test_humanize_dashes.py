"""test_humanize_dashes.py — deterministic em/en-dash remover for reports.

humanize_dashes is an opt-in fixer: it strips em (—) and en (–) dashes from a
report and replaces them with plain punctuation, so the mechanical half of the
humanizer pass never needs an LLM to hand-rewrite dashes. It is deterministic and
conservative — fenced and inline code survive verbatim, number ranges become
ASCII hyphens, ASCII double hyphens (-- / ---) are left alone, and the default is
a dry-run that reports without touching the file. Only --fix mutates.

The last test cross-checks the fixer against check_report_language's detector:
after a fix the detector must report zero `dash` findings, so the two cannot
drift apart on what counts as a dash.
"""
import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import humanize_dashes as hd  # noqa: E402
import check_report_language as crl  # noqa: E402


# --- transformation matrix (humanize_text) -----------------------------------

def test_spaced_em_dash_becomes_comma():
    out, changes = hd.humanize_text("A — B")
    assert out == "A, B"
    assert changes == [1]


def test_number_range_becomes_hyphen():
    assert hd.humanize_text("3–5")[0] == "3-5"
    assert hd.humanize_text("W0–W3")[0] == "W0-W3"


def test_fenced_code_is_preserved():
    text = "intro — note\n```\na — b\n```\ntail — end\n"
    out, changes = hd.humanize_text(text)
    assert "a — b" in out          # fence body untouched
    assert "intro, note" in out    # prose outside fence fixed
    assert "tail, end" in out
    assert 2 not in changes and 3 not in changes  # fence marker + body unchanged


def test_tilde_fenced_code_is_preserved():
    # CommonMark allows ~~~ fences too; their body must survive verbatim.
    text = "intro — note\n~~~\na — b\n~~~\ntail — end\n"
    out, changes = hd.humanize_text(text)
    assert "a — b" in out          # tilde-fence body untouched
    assert "intro, note" in out    # prose outside fence fixed
    assert 2 not in changes and 3 not in changes


def test_inline_code_is_preserved():
    out, _ = hd.humanize_text("use `a — b` and `--flag` here — ok")
    assert "`a — b`" in out      # inline-code dash kept verbatim
    assert "`--flag`" in out
    assert "here, ok" in out     # prose dash outside code fixed


def test_arrows_untouched():
    s = "a -> b and c → d and e => f"
    assert hd.humanize_text(s)[0] == s


def test_ascii_double_hyphen_untouched():
    s = "| --- | --- |\n---\nrun --base-ref now"
    assert hd.humanize_text(s)[0] == s


def test_no_dash_text_is_unchanged():
    s = "plain prose, no dashes here"
    out, changes = hd.humanize_text(s)
    assert out == s and changes == []


def test_replacement_colon_and_period():
    assert hd.humanize_text("A — B", replacement="colon")[0] == "A: B"
    assert hd.humanize_text("A — B", replacement="period")[0] == "A. B"


# --- file I/O: dry-run vs --fix ----------------------------------------------

def _report(tmp_path, body):
    p = tmp_path / "r.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_dry_run_does_not_write(tmp_path):
    p = _report(tmp_path, "A — B\n")
    res = hd.process_file(str(p), fix=False)
    assert res["fixed"] is False
    assert res["count"] == 1
    assert p.read_text(encoding="utf-8") == "A — B\n"   # untouched


def test_fix_writes_in_place_and_is_idempotent(tmp_path):
    p = _report(tmp_path, "A — B and 3–5\n")
    res = hd.process_file(str(p), fix=True)
    assert res["fixed"] is True
    assert p.read_text(encoding="utf-8") == "A, B and 3-5\n"
    # second run: nothing left to change, file not rewritten
    res2 = hd.process_file(str(p), fix=True)
    assert res2["fixed"] is False
    assert res2["count"] == 0


def test_fix_clean_file_not_rewritten(tmp_path):
    p = _report(tmp_path, "no dashes at all\n")
    res = hd.process_file(str(p), fix=True)
    assert res["fixed"] is False
    assert res["count"] == 0


def test_trailing_newline_preserved(tmp_path):
    p = _report(tmp_path, "A — B\n")
    hd.process_file(str(p), fix=True)
    assert p.read_text(encoding="utf-8") == "A, B\n"   # exactly one trailing newline


# --- cross-check against the detector ----------------------------------------

def test_fix_clears_detector_dash_findings(tmp_path):
    body = "Báo cáo — phần một. Khoảng 3–5 mục — xong.\n"
    p = _report(tmp_path, body)
    hd.process_file(str(p), fix=True)
    fixed = p.read_text(encoding="utf-8")
    findings = crl._find(fixed, "vi")
    assert not [f for f in findings if f["kind"] == "dash"]


# --- CLI contract ------------------------------------------------------------

def _run(args):
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "humanize_dashes.py"), *args],
        capture_output=True, text=True,
    )


def test_cli_dry_run_exits_zero_and_reports(tmp_path):
    p = _report(tmp_path, "A — B\n")
    r = _run([str(p)])
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["tool"] == "humanize_dashes"
    assert out["fixed"] is False
    assert out["count"] == 1
    assert p.read_text(encoding="utf-8") == "A — B\n"


def test_cli_fix_rewrites(tmp_path):
    p = _report(tmp_path, "A — B\n")
    r = _run([str(p), "--fix"])
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["fixed"] is True
    assert p.read_text(encoding="utf-8") == "A, B\n"


def test_cli_missing_file_is_inert(tmp_path):
    r = _run([str(tmp_path / "nope.md"), "--fix"])
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("skipped")
