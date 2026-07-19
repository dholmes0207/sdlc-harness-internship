#!/usr/bin/env python3
"""Tests for rbac_selfcheck — the agent_type drift guard.

The script's payload-analysis core is pure and unit-tested here; the real-subagent
spawn that produces a captured payload is the operator's manual/CI step, exercised
in the real-llm test surface, not here.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import rbac_selfcheck as rs  # noqa: E402


class TestExtractRole(unittest.TestCase):
    def test_agent_type_wins(self):
        self.assertEqual(rs.extracted_role({"agent_type": "hs:developer"}), "hs:developer")

    def test_subagent_type_fallback(self):
        self.assertEqual(rs.extracted_role({"subagent_type": "Explore"}), "Explore")

    def test_absent_is_none(self):
        self.assertIsNone(rs.extracted_role({"tool_name": "Write"}))

    def test_empty_string_is_none(self):
        # an empty attribution field is as good as missing — must not pass as a role
        self.assertIsNone(rs.extracted_role({"agent_type": ""}))

    def test_non_string_is_none(self):
        self.assertIsNone(rs.extracted_role({"agent_type": 123}))


class TestAssess(unittest.TestCase):
    def test_subagent_with_role_ok(self):
        r = rs.assess({"agent_type": "hs:developer"}, expect="subagent")
        self.assertTrue(r.ok)
        self.assertFalse(r.drift)
        self.assertEqual(r.role, "hs:developer")

    def test_subagent_missing_role_is_drift(self):
        r = rs.assess({"tool_name": "Write"}, expect="subagent")
        self.assertFalse(r.ok)
        self.assertTrue(r.drift)
        self.assertIsNone(r.role)

    def test_subagent_empty_role_is_drift(self):
        r = rs.assess({"agent_type": ""}, expect="subagent")
        self.assertTrue(r.drift)

    def test_parent_without_role_is_expected_not_drift(self):
        # the top-level agent legitimately carries no agent_type — not a drift signal
        r = rs.assess({"tool_name": "Write"}, expect="parent")
        self.assertTrue(r.ok)
        self.assertFalse(r.drift)


class TestAssessMany(unittest.TestCase):
    def test_all_ok(self):
        payloads = [{"agent_type": "a"}, {"agent_type": "b"}]
        ok, results = rs.assess_many(payloads, expect="subagent")
        self.assertTrue(ok)
        self.assertEqual(len(results), 2)

    def test_one_drift_fails_overall(self):
        payloads = [{"agent_type": "a"}, {"tool_name": "Write"}]
        ok, results = rs.assess_many(payloads, expect="subagent")
        self.assertFalse(ok)


class TestCli(unittest.TestCase):
    def _run(self, args, stdin=None):
        return subprocess.run(
            [sys.executable, str(_SCRIPTS / "rbac_selfcheck.py"), *args],
            input=stdin, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(_SCRIPTS)},
        )

    def test_capture_file_with_role_exits_zero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cap.json"
            p.write_text(json.dumps({"agent_type": "hs:developer", "tool_name": "Write"}))
            cp = self._run(["--capture-file", str(p)])
            self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_capture_file_missing_role_exits_loud(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cap.json"
            p.write_text(json.dumps({"tool_name": "Write"}))
            cp = self._run(["--capture-file", str(p)])
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("DRIFT", cp.stderr.upper())

    def test_capture_file_list_one_drift_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cap.json"
            p.write_text(json.dumps([{"agent_type": "a"}, {"tool_name": "Write"}]))
            cp = self._run(["--capture-file", str(p)])
            self.assertNotEqual(cp.returncode, 0)

    def test_stdin_mode(self):
        cp = self._run(["--stdin"], stdin=json.dumps({"agent_type": "x"}))
        self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_bad_json_exits_usage_error(self):
        cp = self._run(["--stdin"], stdin="{not json")
        self.assertEqual(cp.returncode, 2)

    def test_parent_expectation_no_role_ok(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cap.json"
            p.write_text(json.dumps({"tool_name": "Write"}))
            cp = self._run(["--capture-file", str(p), "--expect", "parent"])
            self.assertEqual(cp.returncode, 0, cp.stderr)


if __name__ == "__main__":
    unittest.main()
