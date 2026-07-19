"""test_test_result_readers.py — normalize heterogeneous test output to one shape.

Three readers, one normalized contract:
  - read_junit   (JUnit-XML, defusedxml)   → {total, failed, errors, skipped, passed}
  - read_cobertura (Cobertura-XML, defusedxml) → {line_rate, branch_rate}
  - read_sarif   (SARIF-JSON, json + hand-written required-fields check, NO
                  jsonschema)               → {results: [{ruleId, severity, level}]}

Hardening invariants:
  - XML goes through defusedxml (billion-laughs / XXE blocked at the parser);
    an over-cap file is refused BEFORE parsing (defense-in-depth size guard).
  - a malformed file raises TestResultError with a clear message — it never
    crashes the gate that calls it.
And the telemetry channel: a normalized run is appended to
harness/state/telemetry/test-execution.jsonl (append-only, actor+ts enriched).
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import test_result_readers as rdr  # noqa: E402

# --- fixtures (py + js shapes) -------------------------------------------------
_JUNIT_SINGLE = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="5" failures="1" errors="0" skipped="1">
  <testcase classname="m" name="a"/>
  <testcase classname="m" name="b"><failure message="boom">tb</failure></testcase>
  <testcase classname="m" name="c"/>
  <testcase classname="m" name="d"><skipped/></testcase>
  <testcase classname="m" name="e"/>
</testsuite>
"""
# jest/mocha emit a <testsuites> root wrapping multiple suites — the reader must
# aggregate across them, not read only the first.
_JUNIT_NESTED = """\
<?xml version="1.0"?>
<testsuites>
  <testsuite name="js-a" tests="2" failures="0" errors="0" skipped="0"/>
  <testsuite name="js-b" tests="3" failures="2" errors="1" skipped="0"/>
</testsuites>
"""
# A non-conformant report that omits the suite-level failures= attribute and
# records the failure only as a <testcase><failure> child. The gate must NOT
# read this as all-pass — a swallowed FAIL would let a failing run clear the DoD.
_JUNIT_TESTCASE_ONLY = """\
<?xml version="1.0"?>
<testsuite name="trimmed" tests="2">
  <testcase name="a"/>
  <testcase name="b"><failure message="boom"/></testcase>
</testsuite>
"""
_COBERTURA = """\
<?xml version="1.0"?>
<coverage line-rate="0.85" branch-rate="0.70" version="1.9">
  <packages/>
</coverage>
"""
# JaCoCo (the Maven coverage engine) expresses coverage as report-level
# <counter type=LINE missed covered> children, NOT a line-rate attribute.
_JACOCO = """\
<?xml version="1.0"?>
<report name="mod">
  <package name="com/x"><class name="C"/></package>
  <counter type="INSTRUCTION" missed="20" covered="80"/>
  <counter type="BRANCH" missed="3" covered="7"/>
  <counter type="LINE" missed="10" covered="90"/>
  <counter type="METHOD" missed="1" covered="9"/>
  <counter type="CLASS" missed="0" covered="2"/>
</report>
"""
_SARIF = json.dumps({
    "version": "2.1.0",
    "runs": [{
        "tool": {"driver": {"name": "bandit"}},
        "results": [
            {"ruleId": "B105", "level": "error",
             "properties": {"security-severity": "8.5"}},
            {"ruleId": "B311", "level": "warning"},
        ],
    }],
})


def _w(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    return p


# --- JUnit ---------------------------------------------------------------------
def test_read_junit_single_suite(tmp_path):
    r = rdr.read_junit(_w(tmp_path / "j.xml", _JUNIT_SINGLE))
    assert r["total"] == 5 and r["failed"] == 1 and r["skipped"] == 1
    assert r["errors"] == 0
    assert r["passed"] == 3  # total - failed - errors - skipped


def test_read_junit_aggregates_nested_suites(tmp_path):
    r = rdr.read_junit(_w(tmp_path / "j.xml", _JUNIT_NESTED))
    assert r["total"] == 5 and r["failed"] == 2 and r["errors"] == 1


def test_read_junit_detects_failure(tmp_path):
    r = rdr.read_junit(_w(tmp_path / "j.xml", _JUNIT_SINGLE))
    assert r["failed"] > 0  # the gate keys off this


def test_read_junit_counts_testcase_level_failure_without_suite_attr(tmp_path):
    # failures= attribute absent → fall back to counting <testcase><failure>
    # children so a failing run is never read as all-pass.
    r = rdr.read_junit(_w(tmp_path / "j.xml", _JUNIT_TESTCASE_ONLY))
    assert r["failed"] == 1  # not swallowed to 0
    assert r["passed"] == 1


# --- Cobertura -----------------------------------------------------------------
def test_read_jacoco_rates(tmp_path):
    # JaCoCo counters → same {line_rate, branch_rate} shape as cobertura so a
    # Java module feeds the same coverage gate.
    r = rdr.read_jacoco(_w(tmp_path / "jacoco.xml", _JACOCO))
    assert abs(r["line_rate"] - 0.90) < 1e-9   # 90 covered / 100 total
    assert abs(r["branch_rate"] - 0.70) < 1e-9  # 7 / 10


def test_read_jacoco_rejects_non_jacoco(tmp_path):
    try:
        rdr.read_jacoco(_w(tmp_path / "x.xml", _COBERTURA))
    except rdr.TestResultError:
        return
    raise AssertionError("a non-JaCoCo XML must raise TestResultError")


def test_read_cobertura_rates(tmp_path):
    r = rdr.read_cobertura(_w(tmp_path / "c.xml", _COBERTURA))
    assert abs(r["line_rate"] - 0.85) < 1e-9
    assert abs(r["branch_rate"] - 0.70) < 1e-9


# --- SARIF ---------------------------------------------------------------------
def test_read_sarif_results(tmp_path):
    r = rdr.read_sarif(_w(tmp_path / "s.sarif", _SARIF))
    assert len(r["results"]) == 2
    first = r["results"][0]
    assert first["ruleId"] == "B105" and first["level"] == "error"
    # an error-level finding maps to a high/critical severity bucket
    assert first["severity"] in ("high", "critical")


def test_read_sarif_missing_required_field_raises(tmp_path):
    # no top-level `runs` → mini validator must reject (not crash).
    bad = _w(tmp_path / "s.sarif", json.dumps({"version": "2.1.0"}))
    try:
        rdr.read_sarif(bad)
    except rdr.TestResultError as e:
        assert "runs" in str(e).lower()
    else:
        raise AssertionError("a SARIF doc with no runs[] must raise")


# --- hardening -----------------------------------------------------------------
def test_malformed_xml_raises_clear_error(tmp_path):
    bad = _w(tmp_path / "j.xml", "<testsuite tests='1'><broken")
    try:
        rdr.read_junit(bad)
    except rdr.TestResultError:
        pass
    else:
        raise AssertionError("malformed XML must raise TestResultError, not crash")


def test_oversize_file_refused_before_parse(tmp_path):
    big = _w(tmp_path / "j.xml", _JUNIT_SINGLE)
    try:
        rdr.read_junit(big, max_bytes=10)  # tiny cap → refuse before parse
    except rdr.TestResultError as e:
        assert "size" in str(e).lower() or "cap" in str(e).lower()
    else:
        raise AssertionError("an over-cap file must be refused before parsing")


def test_missing_file_raises(tmp_path):
    try:
        rdr.read_junit(tmp_path / "nope.xml")
    except rdr.TestResultError:
        pass
    else:
        raise AssertionError("a missing result file must raise TestResultError")


# --- telemetry channel ---------------------------------------------------------
def test_emit_test_execution_appends_record(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    # telemetry_paths self-disables under pytest; clear the marker so the real
    # append path runs and we can assert the channel was written.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    rdr.emit_test_execution(change_class="feature",
                            signals=["src_changed", "test_has_assertions"],
                            test_types=["unit", "integration"],
                            verdict="PASS")
    sink = tmp_path / "telemetry" / "test-execution.jsonl"
    assert sink.is_file(), "test-execution.jsonl channel must be written"
    rec = json.loads(sink.read_text().splitlines()[0])
    assert rec["change_class"] == "feature"
    assert rec["verdict"] == "PASS"
    assert "actor" in rec and "ts" in rec  # enriched by telemetry_paths
    assert rec["test_types"] == ["unit", "integration"]
