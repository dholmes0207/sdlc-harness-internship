"""test_coverage_merge_per_lang.py — coverage is judged PER LANGUAGE, never merged.

R3: there is no fair way to average coverage across stacks (a 90%-line Python
report and a 40%-line JS report do not combine into one honest number). So the
gate keeps per-language Cobertura reports separate and compares each to its own
threshold; it never produces a single global percentage. py-pass / js-fail is
reported as js-fail, not washed out by py.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import perf_telemetry as cov  # coverage-per-lang lives alongside perf in the P3 module


def _cobertura(rate):
    return '<coverage line-rate="%s" branch-rate="0.5"/>' % rate


def test_per_language_kept_separate(tmp_path):
    py = tmp_path / "py.xml"
    js = tmp_path / "js.xml"
    py.write_text(_cobertura(0.92), encoding="utf-8")
    js.write_text(_cobertura(0.40), encoding="utf-8")
    per = cov.coverage_per_language({"python": py, "javascript": js})
    assert abs(per["python"]["line_rate"] - 0.92) < 1e-9
    assert abs(per["javascript"]["line_rate"] - 0.40) < 1e-9
    # no global/merged key — judged per stack only.
    assert "global" not in per and "merged" not in per


def test_py_pass_js_fail_reports_the_failing_stack(tmp_path):
    py = tmp_path / "py.xml"
    js = tmp_path / "js.xml"
    py.write_text(_cobertura(0.92), encoding="utf-8")
    js.write_text(_cobertura(0.40), encoding="utf-8")
    per = cov.coverage_per_language({"python": py, "javascript": js})
    ok, failures = cov.coverage_meets_per_lang(
        per, {"python": 80, "javascript": 80})
    assert ok is False
    assert any("javascript" in f for f in failures)
    assert not any("python" in f for f in failures)  # python is fine


def test_all_stacks_pass(tmp_path):
    py = tmp_path / "py.xml"
    py.write_text(_cobertura(0.85), encoding="utf-8")
    per = cov.coverage_per_language({"python": py})
    ok, failures = cov.coverage_meets_per_lang(per, {"python": 80})
    assert ok is True and failures == []
