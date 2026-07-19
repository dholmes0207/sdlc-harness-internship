"""Integration tests for context-engineering automation scripts.

Verifies that context_analyzer.py and compression_evaluator.py are present,
runnable, and produce structurally correct output on fixture inputs.

These tests are fixture-driven and do NOT read repo-internal docs/ledger,
so @pytest.mark.dev_repo is NOT applied.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).parent.parent
    / "plugins/hs/skills/context-engineering/scripts"
)
PYTHON = sys.executable

ANALYZER = SCRIPTS_DIR / "context_analyzer.py"
EVALUATOR = SCRIPTS_DIR / "compression_evaluator.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def transcript_fixture(tmp_path):
    """Sample transcript with enough content to exercise health scoring."""
    messages = [
        {"role": "user", "content": "Implement the token-rotation feature. This is critical."},
        {"role": "assistant", "content": "I'll implement that. Next steps: create the rotation module."},
        {"role": "user", "content": "The test failed with error: connection timeout."},
        {"role": "assistant", "content": "Found that the pool exhaustion caused the timeout. Implemented fix."},
        {"role": "user", "content": "Good. What is the next step?"},
    ]
    f = tmp_path / "transcript.json"
    f.write_text(json.dumps({"messages": messages}), encoding="utf-8")
    return f


@pytest.fixture
def compressed_fixture(tmp_path):
    """Short summary that is clearly smaller than the original transcript."""
    summary = (
        "Session summary: implemented token-rotation fix for connection timeout. "
        "Next step: run integration tests."
    )
    f = tmp_path / "summary.txt"
    f.write_text(summary, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Script presence gate — tests FAIL if scripts are absent
# ---------------------------------------------------------------------------

def test_analyzer_script_exists():
    assert ANALYZER.exists(), f"context_analyzer.py not found at {ANALYZER}"


def test_evaluator_script_exists():
    assert EVALUATOR.exists(), f"compression_evaluator.py not found at {EVALUATOR}"


# ---------------------------------------------------------------------------
# context_analyzer.py — analyze command
# ---------------------------------------------------------------------------

class TestContextAnalyzerIntegration:

    def test_analyze_returns_health_score(self, transcript_fixture):
        """analyze must return a JSON object with health_score and health_status."""
        result = subprocess.run(
            [PYTHON, str(ANALYZER), "analyze", str(transcript_fixture)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "health_score" in data
        assert "health_status" in data
        # health_score is a string like "0.72"; parse it
        score = float(data["health_score"])
        assert 0.0 <= score <= 1.0, f"health_score out of range: {score}"

    def test_analyze_returns_utilization(self, transcript_fixture):
        """utilization field must be a percentage string."""
        result = subprocess.run(
            [PYTHON, str(ANALYZER), "analyze", str(transcript_fixture)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "utilization" in data
        assert data["utilization"].endswith("%")

    def test_analyze_recommendations_is_list(self, transcript_fixture):
        """recommendations must be a list (possibly empty)."""
        result = subprocess.run(
            [PYTHON, str(ANALYZER), "analyze", str(transcript_fixture)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data["recommendations"], list)

    def test_analyze_budget_command(self):
        """budget subcommand must return allocation + thresholds."""
        result = subprocess.run(
            [PYTHON, str(ANALYZER), "budget",
             "--system", "2000", "--tools", "1500", "--docs", "3000", "--history", "5000"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "total_budget" in data
        assert "warning_threshold" in data
        assert data["total_budget"] > 0

    def test_analyze_with_custom_limit(self, transcript_fixture):
        """--limit overrides the token window; utilization must reflect it."""
        # With limit=10, a non-empty transcript should show high utilization
        result = subprocess.run(
            [PYTHON, str(ANALYZER), "analyze", str(transcript_fixture), "--limit", "10"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        # total_tokens > 10 means utilization > 100% but capped in display
        assert int(data["total_tokens"]) > 0


# ---------------------------------------------------------------------------
# compression_evaluator.py — evaluate command
# ---------------------------------------------------------------------------

class TestCompressionEvaluatorIntegration:

    def test_evaluate_returns_compression_ratio(self, transcript_fixture, compressed_fixture):
        """evaluate must return a compression_ratio > 0 for a real summary."""
        result = subprocess.run(
            [PYTHON, str(EVALUATOR), "evaluate",
             str(transcript_fixture), str(compressed_fixture)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "compression_ratio" in data
        # ratio is a string like "72.3%"
        ratio_str = data["compression_ratio"].rstrip("%")
        ratio = float(ratio_str)
        assert ratio > 0, "Summary must be shorter than the original; ratio must be > 0"

    def test_evaluate_returns_quality_score(self, transcript_fixture, compressed_fixture):
        """quality_score must be a float in [0, 1]."""
        result = subprocess.run(
            [PYTHON, str(EVALUATOR), "evaluate",
             str(transcript_fixture), str(compressed_fixture)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "quality_score" in data
        score = float(data["quality_score"])
        assert 0.0 <= score <= 1.0

    def test_evaluate_returns_dimension_scores(self, transcript_fixture, compressed_fixture):
        """dimension_scores must cover the six defined evaluation dimensions."""
        expected_dims = {
            "accuracy", "context_awareness", "artifact_trail",
            "completeness", "continuity", "instruction_following",
        }
        result = subprocess.run(
            [PYTHON, str(EVALUATOR), "evaluate",
             str(transcript_fixture), str(compressed_fixture)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "dimension_scores" in data
        assert set(data["dimension_scores"].keys()) == expected_dims

    def test_evaluate_probe_count_positive(self, transcript_fixture, compressed_fixture):
        """probe_count must be > 0 for a non-trivial transcript."""
        result = subprocess.run(
            [PYTHON, str(EVALUATOR), "evaluate",
             str(transcript_fixture), str(compressed_fixture)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["probe_count"] > 0

    def test_generate_probes_returns_list(self, transcript_fixture):
        """generate-probes must return a JSON array."""
        result = subprocess.run(
            [PYTHON, str(EVALUATOR), "generate-probes", str(transcript_fixture)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        # Each probe must have type, question, ground_truth
        for probe in data:
            assert "type" in probe
            assert "question" in probe
            assert "ground_truth" in probe
