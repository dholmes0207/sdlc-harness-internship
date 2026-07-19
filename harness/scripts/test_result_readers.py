#!/usr/bin/env python3
"""test_result_readers.py — normalize heterogeneous test output to one shape.

Three readers feed the DoD gate one comparable contract:
  - read_junit(path)     → {total, failed, errors, skipped, passed}
  - read_cobertura(path) → {line_rate, branch_rate}
  - read_sarif(path)     → {results: [{ruleId, severity, level}]}

Security posture: XML output of a test run is UNTRUSTED in a CI
pull-request context — a malicious fixture can carry a billion-laughs/XXE bomb.
stdlib ElementTree is NOT safe against that, so XML goes through `defusedxml`
(a controlled dependency declared in preflight_deps.py); a coarse size cap
refuses an over-large file BEFORE the parser sees it (defense-in-depth). SARIF
is JSON — read with stdlib `json` and a HAND-WRITTEN required-fields check (no
jsonschema dependency).

Every reader raises `TestResultError` (clear, actionable) on a missing,
over-cap, or malformed file — it must never crash the gate that calls it.

The telemetry channel (emit_test_execution) appends a normalized run to
harness/state/telemetry/test-execution.jsonl via telemetry_paths (actor+ts
enriched, append-only, fail-open).
"""

import json
import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 12 MB: a real JUnit/Cobertura/SARIF report is KB–low-MB; anything past this is
# either a runaway export or an attack payload. Refused before parsing.
MAX_RESULT_BYTES = 12 * 1024 * 1024

# SARIF level → severity bucket. The gate keys off severity; `level` is
# kept verbatim for traceability.
_LEVEL_SEVERITY = {"error": "high", "warning": "medium", "note": "low",
                   "none": "low"}


class TestResultError(Exception):
    """A result file is missing, over the size cap, or malformed. The message
    names the path + the problem so the caller surfaces a fix, never a stack
    trace from inside the gate."""


def _read_capped(path, max_bytes: int) -> str:
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as e:
        raise TestResultError("test result %s is unreadable: %s" % (p, e))
    if size > max_bytes:
        raise TestResultError(
            "test result %s is %d bytes — over the %d-byte size cap; refused "
            "before parsing (an oversized XML report is treated as a possible "
            "bomb)" % (p, size, max_bytes))
    try:
        return p.read_text(encoding="utf-8")
    except OSError as e:
        raise TestResultError("test result %s is unreadable: %s" % (p, e))


def _parse_xml(path, max_bytes: int):
    """defusedxml-parsed root element, or TestResultError. defusedxml blocks
    entity-expansion / external-entity attacks; we add the size cap on top."""
    text = _read_capped(path, max_bytes)
    try:
        from defusedxml.ElementTree import fromstring
    except ImportError:
        raise TestResultError(
            "defusedxml is required to read XML test results safely — install "
            "it: pip install defusedxml (it is declared in preflight_deps.py)")
    try:
        return fromstring(text)
    except Exception as e:  # noqa: BLE001 — any parse failure → one clear error
        raise TestResultError("test result %s is malformed XML: %s" % (path, e))


def _int_attr(elem, name: str) -> int:
    try:
        return int(elem.get(name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def read_junit(path, *, max_bytes: int = MAX_RESULT_BYTES) -> dict:
    """Aggregate a JUnit-XML report into {total, failed, errors, skipped,
    passed}. Handles both a single <testsuite> root and a <testsuites> root
    wrapping many suites (jest/mocha) by summing every testsuite's attributes."""
    root = _parse_xml(path, max_bytes)
    # Element.iter(tag) includes the element ITSELF when it matches, so this one
    # call covers both a single <testsuite> root and a <testsuites> wrapper.
    suites = list(root.iter("testsuite"))
    total = sum(_int_attr(s, "tests") for s in suites)
    failed = sum(_int_attr(s, "failures") for s in suites)
    errors = sum(_int_attr(s, "errors") for s in suites)
    # A report may omit the suite-level failures=/errors= summary and record a
    # failure only as a <testcase><failure>/<error> child. When the summary says
    # zero, fall back to counting those children so a failing run is never read
    # as all-pass — the DoD gate keys off failed+errors.
    if failed == 0:
        failed = sum(1 for tc in root.iter("testcase")
                     if tc.find("failure") is not None)
    if errors == 0:
        errors = sum(1 for tc in root.iter("testcase")
                     if tc.find("error") is not None)
    skipped = sum(_int_attr(s, "skipped") for s in suites)
    passed = max(0, total - failed - errors - skipped)
    return {"total": total, "failed": failed, "errors": errors,
            "skipped": skipped, "passed": passed}


def _float_attr(elem, name: str):
    try:
        return float(elem.get(name))
    except (TypeError, ValueError):
        return None


def read_cobertura(path, *, max_bytes: int = MAX_RESULT_BYTES) -> dict:
    """Read a Cobertura coverage report's root rates → {line_rate, branch_rate}
    as floats in [0, 1] (or None when the attribute is absent)."""
    root = _parse_xml(path, max_bytes)
    cov = root if root.tag == "coverage" else next(
        (e for e in root.iter("coverage")), None)
    if cov is None:
        raise TestResultError(
            "test result %s is not a Cobertura report (no <coverage> element)"
            % path)
    return {"line_rate": _float_attr(cov, "line-rate"),
            "branch_rate": _float_attr(cov, "branch-rate")}


def read_jacoco(path, *, max_bytes: int = MAX_RESULT_BYTES) -> dict:
    """Read a JaCoCo XML report's aggregate counters → {line_rate, branch_rate}
    (same shape as read_cobertura, so a Java module feeds the SAME coverage gate
    as a Python/JS one). JaCoCo expresses coverage as report-level
    <counter type="LINE" missed covered> children — not a line-rate attribute —
    so the rate is covered/(covered+missed). None when a counter is absent."""
    root = _parse_xml(path, max_bytes)
    rep = root if root.tag == "report" else next(
        (e for e in root.iter("report")), None)
    if rep is None:
        raise TestResultError(
            "test result %s is not a JaCoCo report (no <report> element)" % path)
    rates = {}
    # report-level counters are DIRECT children of <report> (the aggregate); the
    # per-package/class counters nested below are intentionally not summed here.
    for c in rep:
        if c.tag != "counter":
            continue
        ctype = (c.get("type") or "").upper()
        if ctype not in ("LINE", "BRANCH"):
            continue
        total = _int_attr(c, "covered") + _int_attr(c, "missed")
        rate = (_int_attr(c, "covered") / total) if total > 0 else None
        rates["line_rate" if ctype == "LINE" else "branch_rate"] = rate
    return {"line_rate": rates.get("line_rate"),
            "branch_rate": rates.get("branch_rate")}


def _validate_sarif(doc, path) -> None:
    """Hand-written required-fields check (NO jsonschema): a SARIF log is a dict
    with `version` and a `runs` LIST, each run a dict with a `results` LIST.
    Anything else raises so a non-SARIF JSON cannot pass as an empty clean run."""
    if not isinstance(doc, dict):
        raise TestResultError("SARIF %s is not a JSON object" % path)
    if "version" not in doc:
        raise TestResultError("SARIF %s missing required field `version`" % path)
    runs = doc.get("runs")
    if not isinstance(runs, list):
        raise TestResultError(
            "SARIF %s missing/invalid `runs` — expected a list of runs" % path)
    for i, run in enumerate(runs):
        if not isinstance(run, dict) or not isinstance(run.get("results"), list):
            raise TestResultError(
                "SARIF %s runs[%d] missing/invalid `results` list" % (path, i))


def _severity_of(result: dict) -> str:
    """Severity bucket for one SARIF result. An explicit
    properties.security-severity (CVSS-like 0-10) wins; else map from `level`."""
    props = result.get("properties") or {}
    raw = props.get("security-severity")
    if raw is not None:
        try:
            score = float(raw)
            if score >= 9.0:
                return "critical"
            if score >= 7.0:
                return "high"
            if score >= 4.0:
                return "medium"
            return "low"
        except (TypeError, ValueError):
            pass
    return _LEVEL_SEVERITY.get(str(result.get("level", "warning")).lower(),
                               "medium")


def read_sarif(path, *, max_bytes: int = MAX_RESULT_BYTES) -> dict:
    """Read a SARIF log → {results: [{ruleId, severity, level}]}. json (stdlib)
    + a hand-written required-fields check; raises TestResultError on a missing,
    over-cap, or malformed file."""
    text = _read_capped(path, max_bytes)
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise TestResultError("SARIF %s is malformed JSON: %s" % (path, e))
    _validate_sarif(doc, path)
    out = []
    for run in doc["runs"]:
        for res in run["results"]:
            if not isinstance(res, dict):
                continue
            out.append({
                "ruleId": res.get("ruleId"),
                "level": res.get("level", "warning"),
                "severity": _severity_of(res),
            })
    return {"results": out}


def sarif_verdict(sarif: dict):
    """(verdict, detail) for a normalized SARIF result set (read_sarif output).
    FAIL when any finding is error-level OR high/critical severity — those are
    the actionable buckets a security/a11y gate must block on; warning/note are
    advisory. detail names the blocking count so the gate reason is concrete."""
    blocking = [r for r in (sarif.get("results") or [])
                if str(r.get("level", "")).lower() == "error"
                or str(r.get("severity", "")).lower() in ("high", "critical")]
    if blocking:
        return "FAIL", ("%d error/high-severity finding(s): %s"
                        % (len(blocking),
                           ", ".join(sorted({str(r.get("ruleId")) for r in blocking
                                             if r.get("ruleId")}))[:200]))
    return "PASS", "no error/high-severity findings"


# --- telemetry channel --------------------------------------------------------
_TELEMETRY_SINK = "test-execution.jsonl"


def build_test_execution_record(change_class, signals, test_types, verdict,
                                grace=None) -> dict:
    """The normalized telemetry record (actor + ts are added by telemetry_paths
    on append). Pure — separated from the write so the shape is unit-testable."""
    rec = {
        "change_class": change_class,
        "signals": list(signals or []),
        "test_types": list(test_types or []),
        "verdict": verdict,
    }
    if grace is not None:
        rec["grace"] = grace
    return rec


def emit_test_execution(change_class, signals, test_types, verdict,
                        grace=None) -> None:
    """Append one normalized run to the test-execution telemetry channel.
    Fail-open (telemetry must never break the gate)."""
    try:
        import telemetry_paths
        telemetry_paths.append_event(
            _TELEMETRY_SINK,
            build_test_execution_record(change_class, signals, test_types,
                                        verdict, grace))
    except Exception:  # noqa: BLE001 — telemetry is fail-open by contract
        pass
