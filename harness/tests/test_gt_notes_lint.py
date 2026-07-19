"""Contract for gt_notes_lint.py — the deterministic notes-lint layer of the
lỗ #1 fix (GT authored by copying production output instead of deriving from
spec). It scans ground_truth.json items' `notes`/`note` for bilingual
source-indicator phrases (the author confessing in prose that the answer key
came from the running system) and FLAGS them, advisory. The false-positive
boundary matters: good wording ("spec-derived, cross-checked vs production")
must NOT flag; only phrases that name production/the code as the SOURCE do.
"""

import json
import subprocess
import sys

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "gt_notes_lint.py"


def _run(gt_path, strict=False):
    args = [sys.executable, str(_SCRIPT), "--ground-truth", str(gt_path)]
    if strict:
        args += ["--strict"]
    return subprocess.run(args, capture_output=True, text=True)


def _write_gt(tmp_path, items):
    gt = tmp_path / "ground_truth.json"
    gt.write_text(json.dumps({"description": "t", "items": items}, ensure_ascii=False),
                  encoding="utf-8")
    return gt


def test_flags_en_verified_against_production(tmp_path):
    gt = _write_gt(tmp_path, [
        {"case_file": "c1", "ground_truth": {"name": "A"},
         "notes": "judged from AC; verified against production confidence.evaluate"}])
    r = _run(gt)
    assert r.returncode == 0, r.stderr
    assert "FLAG" in r.stdout
    assert "c1" in r.stdout


def test_flags_vi_lay_tu_output(tmp_path):
    gt = _write_gt(tmp_path, [
        {"case_file": "c2", "ground_truth": {"name": "B"},
         "notes": "đáp án lấy từ output production"}])
    r = _run(gt)
    assert r.returncode == 0, r.stderr
    assert "FLAG" in r.stdout
    assert "c2" in r.stdout


def test_clean_notes_no_flag(tmp_path):
    gt = _write_gt(tmp_path, [
        {"case_file": "c3", "ground_truth": {"name": "C"},
         "notes": "spec-derived from AC-11 checksum rule"}])
    r = _run(gt)
    assert r.returncode == 0, r.stderr
    assert "FLAG" not in r.stdout


def test_cross_checked_wording_no_flag(tmp_path):
    # the RECOMMENDED good wording must not be flagged (false-positive boundary)
    gt = _write_gt(tmp_path, [
        {"case_file": "c4", "ground_truth": {"name": "D"},
         "notes": "spec-derived, cross-checked vs production"}])
    r = _run(gt)
    assert r.returncode == 0, r.stderr
    assert "FLAG" not in r.stdout


def test_advisory_exit0_strict_exit1(tmp_path):
    gt = _write_gt(tmp_path, [
        {"case_file": "c5", "ground_truth": {"name": "E"},
         "notes": "answer copied from output"}])
    assert _run(gt).returncode == 0
    assert _run(gt, strict=True).returncode == 1


def test_null_and_empty_notes_no_flag(tmp_path):
    gt = _write_gt(tmp_path, [
        {"case_file": "c6", "ground_truth": {"name": "F"}},
        {"case_file": "c7", "ground_truth": {"name": "G"}, "notes": ""}])
    r = _run(gt)
    assert r.returncode == 0, r.stderr
    assert "FLAG" not in r.stdout


def test_no_false_positive_on_codebase_word(tmp_path):
    # "codebase" must not trip "the code"; "by executing the spec" must not trip
    # "by executing" as a source confession -- word boundaries, not substring.
    for clean in ("derived from the codebase standards doc",
                  "I ran the codebase linter",
                  "computed by executing the spec formula"):
        gt = _write_gt(tmp_path, [
            {"case_file": "c", "ground_truth": {"x": 1}, "notes": clean}])
        r = _run(gt)
        assert r.returncode == 0, r.stderr
        assert "FLAG" not in r.stdout, "false positive on %r" % clean


def test_flags_from_the_code_still_fires(tmp_path):
    gt = _write_gt(tmp_path, [
        {"case_file": "c", "ground_truth": {"x": 1},
         "notes": "answer taken from the code directly"}])
    r = _run(gt)
    assert "FLAG" in r.stdout


def test_overlapping_phrase_reported_once(tmp_path):
    # 'verified against production' also contains 'verified against prod' — the
    # dedup must report the case only once.
    gt = _write_gt(tmp_path, [
        {"case_file": "solo", "ground_truth": {"x": 1},
         "notes": "verified against production"}])
    r = _run(gt)
    assert r.stdout.count("FLAG: solo:") == 1


def test_flags_confession_split_across_newline(tmp_path):
    # a source confession split by a newline / extra spaces must still flag
    gt = _write_gt(tmp_path, [
        {"case_file": "c", "ground_truth": {"x": 1},
         "notes": "verified against\nproduction"}])
    r = _run(gt)
    assert "FLAG" in r.stdout


def test_no_false_positive_on_hyphen_prefix(tmp_path):
    # "non-production output" must not trip "production output"
    gt = _write_gt(tmp_path, [
        {"case_file": "c", "ground_truth": {"x": 1},
         "notes": "sampled from the non-production output mirror"}])
    r = _run(gt)
    assert "FLAG" not in r.stdout


def test_empty_notes_falls_back_to_note_field(tmp_path):
    gt = _write_gt(tmp_path, [
        {"case_file": "c", "ground_truth": {"x": 1}, "notes": "",
         "note": "answer copied from output"}])
    r = _run(gt)
    assert "FLAG" in r.stdout


def test_bilingual_constant_nonempty():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gt_notes_lint", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    phrases = mod.SUSPECT_PHRASES
    # at least one Vietnamese and one English source-indicator phrase pinned
    assert any("production" in p for p in phrases)          # EN base
    assert any("lấy từ" in p or "đối chiếu" in p for p in phrases)  # VI base
