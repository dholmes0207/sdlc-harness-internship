"""test_score_skill_description.py — format compliance scorer for skill descriptions.

Tests for harness/scripts/score_skill_description.py. Covers the 5 deterministic
structural criteria (length, action-verb, trigger phrase, use-case count, boundary),
and confusable-pair detection via Jaccard similarity.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import score_skill_description as ssd  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill(name: str, description: str) -> dict:
    return {"name": name, "description": description}


# ---------------------------------------------------------------------------
# Length scoring
# ---------------------------------------------------------------------------

class TestLengthScore:
    def test_too_short_scores_zero(self):
        score, issues = ssd._score_length("Hi")
        assert score == 0.0
        assert any("short" in i.lower() for i in issues)

    def test_below_optimal_scores_half(self):
        # 40 chars — above min (20) but below optimal (80)
        desc = "A" * 40
        score, issues = ssd._score_length(desc)
        assert score == 0.5

    def test_optimal_range_scores_full(self):
        # 100 chars — in optimal range (80-200)
        desc = "A" * 100
        score, issues = ssd._score_length(desc)
        assert score == 1.0
        assert issues == []

    def test_slightly_long_scores_partial(self):
        # 250 chars — long but within 300
        desc = "A" * 250
        score, issues = ssd._score_length(desc)
        assert score == 0.8

    def test_very_long_scores_half(self):
        # 350 chars — over 300
        desc = "A" * 350
        score, issues = ssd._score_length(desc)
        assert score == 0.5


# ---------------------------------------------------------------------------
# Action-verb scoring
# ---------------------------------------------------------------------------

class TestVerbScore:
    def test_action_verb_start_scores_full(self):
        score, issues = ssd._score_verb("Analyze the codebase for issues.")
        assert score == 1.0
        assert issues == []

    def test_no_action_verb_scores_zero(self):
        score, issues = ssd._score_verb("This is a description without a verb.")
        assert score == 0.0
        assert issues

    def test_second_word_is_verb_scores_half(self):
        # First word is not an action verb, second word is
        score, issues = ssd._score_verb("ALWAYS analyze the thing when needed.")
        assert score == 0.5

    def test_case_insensitive_verb_match(self):
        score, _ = ssd._score_verb("analyze dependencies and report findings.")
        assert score == 1.0


# ---------------------------------------------------------------------------
# Trigger phrase scoring
# ---------------------------------------------------------------------------

class TestTriggerScore:
    def test_use_for_scores_full(self):
        score, issues = ssd._score_trigger("Do something. Use for debugging, profiling, analysis.")
        assert score == 1.0
        assert issues == []

    def test_use_when_scores_full(self):
        score, issues = ssd._score_trigger("Do something. Use when the test fails unexpectedly.")
        assert score == 1.0
        assert issues == []

    def test_missing_trigger_scores_zero(self):
        score, issues = ssd._score_trigger("Does a thing with no trigger clause at all here.")
        assert score == 0.0
        assert issues

    def test_broad_for_scores_partial(self):
        # Has "for" with trailing context but no explicit "Use for/when"
        score, issues = ssd._score_trigger("Runs tests for continuous integration pipelines.")
        assert score == 0.7
        assert issues

    def test_case_insensitive_trigger(self):
        score, _ = ssd._score_trigger("Debug a failure. use when a test suite fails repeatedly.")
        assert score == 1.0


# ---------------------------------------------------------------------------
# Use-case count scoring
# ---------------------------------------------------------------------------

class TestUsecaseScore:
    def test_no_usecases_scores_zero(self):
        # No comma-separated items at all — no trigger, no period, empty text
        score, issues = ssd._score_usecases("")
        assert score == 0.0
        assert issues

    def test_single_usecase_after_trigger_scores_half(self):
        # "Use when needed." — one segment ("needed.") after trigger
        score, issues = ssd._score_usecases("Do something. Use when needed.")
        assert score == 0.5
        assert issues

    def test_one_usecase_scores_half(self):
        score, issues = ssd._score_usecases("Do something. Use for debugging only.")
        assert score == 0.5
        assert issues

    def test_two_to_four_usecases_scores_full(self):
        score, issues = ssd._score_usecases(
            "Do something. Use when debugging, profiling, or tracing issues."
        )
        assert score == 1.0
        assert issues == []

    def test_too_many_usecases_scores_partial(self):
        score, issues = ssd._score_usecases(
            "Do. Use for a, b, c, d, e, f, g things."
        )
        assert score == 0.8
        assert issues


# ---------------------------------------------------------------------------
# score_description: full integration
# ---------------------------------------------------------------------------

class TestScoreDescription:
    def test_well_formed_description_scores_high(self):
        desc = (
            "Analyze the codebase for structural issues. "
            "Use when reviewing a pull request, auditing dependencies, or checking lint compliance. "
            "Supports Python, JavaScript, and TypeScript projects."
        )
        result = ssd.score_description("analyze-skill", desc)
        assert result.total >= ssd.PASS_THRESHOLD
        assert result.passed

    def test_vague_description_scores_low(self):
        desc = "Helps."  # too short, no verb, no trigger
        result = ssd.score_description("vague-skill", desc)
        assert result.total < ssd.PASS_THRESHOLD
        assert not result.passed


    def test_total_uses_correct_weights(self):
        # Verify weight math: set known component values and check total
        result = ssd.score_description("sk", "A" * 100)
        # boundary_score defaults 1.0, length 1.0, verb 0.0, trigger 0.0, usecases 0.0
        expected = (
            1.0 * ssd.W_LENGTH
            + result.verb_score * ssd.W_VERB
            + result.trigger_score * ssd.W_TRIGGER
            + result.usecase_score * ssd.W_USECASE
            + 1.0 * ssd.W_BOUNDARY
        )
        assert abs(result.total - expected) < 1e-9


# ---------------------------------------------------------------------------
# Confusable pair detection (Jaccard)
# ---------------------------------------------------------------------------

class TestConfusablePairs:
    def test_near_identical_descriptions_flagged(self):
        skills = [
            _skill("skill-a", "Analyze codebase for structural issues and report findings."),
            _skill("skill-b", "Analyze codebase for structural issues and report findings."),
        ]
        pairs = ssd.check_confusable_pairs(skills)
        names = {(a, b) for a, b, _ in pairs}
        assert ("skill-a", "skill-b") in names

    def test_distinct_descriptions_not_flagged(self):
        skills = [
            _skill("skill-a", "Deploy containers to Kubernetes using Helm charts and rollbacks."),
            _skill("skill-b", "Write unit tests with mocks, fixtures, and edge case coverage."),
        ]
        pairs = ssd.check_confusable_pairs(skills)
        assert pairs == []

    def test_empty_descriptions_skipped(self):
        skills = [
            _skill("skill-a", ""),
            _skill("skill-b", ""),
        ]
        # Empty tokenized sets are skipped — no pair reported
        pairs = ssd.check_confusable_pairs(skills)
        assert pairs == []

    def test_similarity_value_is_between_zero_and_one(self):
        skills = [
            _skill("a", "Analyze code for issues, bugs, and problems in the codebase."),
            _skill("b", "Analyze code for issues, bugs, and problems in the codebase."),
        ]
        pairs = ssd.check_confusable_pairs(skills)
        assert pairs
        _, _, sim = pairs[0]
        assert 0.0 <= sim <= 1.0

    def test_threshold_not_exceeded_for_moderate_overlap(self):
        # Descriptions share some words but not enough to hit 0.80 Jaccard
        skills = [
            _skill("a", "Run linting checks and report errors to the developer on demand."),
            _skill("b", "Generate documentation from source code files and update the wiki."),
        ]
        pairs = ssd.check_confusable_pairs(skills)
        assert pairs == []


# ---------------------------------------------------------------------------
# CLI: walk plugin dirs, --json output
# ---------------------------------------------------------------------------

class TestDiscoverSkills:
    def test_disabled_skills_sibling_is_skipped(self, tmp_path):
        # A live skill under skills/ is discovered; an off skill parked in the
        # sibling disabled-skills/ tree is NOT — discovery mirrors the loader, which
        # only sees skills/. Otherwise a disabled skill would be scored as active.
        live = tmp_path / "plugins" / "hs" / "skills" / "live-one"
        live.mkdir(parents=True)
        (live / "SKILL.md").write_text(
            "---\nname: hs:live-one\ndescription: Do the live thing. Use when running.\n---\n",
            encoding="utf-8")
        off = tmp_path / "plugins" / "hs" / "disabled-skills" / "off-one"
        off.mkdir(parents=True)
        (off / "SKILL.md").write_text(
            "---\nname: hs:off-one\ndescription: Parked and off. Use never.\n---\n",
            encoding="utf-8")
        names = {s["name"] for s in ssd.discover_skills(tmp_path / "plugins")}
        assert "hs:live-one" in names
        assert "hs:off-one" not in names


class TestCLI:
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_SCRIPTS / "score_skill_description.py"), *args],
            capture_output=True, text=True,
        )

    def test_cli_json_output_is_valid_json(self, tmp_path):
        # Build a minimal plugin-style skills dir
        skill_dir = tmp_path / "plugins" / "hs" / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        desc = (
            "Analyze the project for structural issues. "
            "Use when debugging, reviewing PRs, or tracing failures. "
            "Supports Python and TypeScript."
        )
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: hs:demo-skill\ndescription: {desc}\n---\n# Body\n",
            encoding="utf-8",
        )
        r = self._run(str(tmp_path / "plugins"), "--json")
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert isinstance(data, dict)
        assert "scores" in data

    def test_cli_human_report_exits_zero(self, tmp_path):
        skill_dir = tmp_path / "plugins" / "hs" / "skills" / "s"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: hs:s\ndescription: Analyze code. Use when reviewing or testing.\n---\n# B\n",
            encoding="utf-8",
        )
        r = self._run(str(tmp_path / "plugins"))
        assert r.returncode == 0

    def test_cli_empty_dir_exits_zero(self, tmp_path):
        r = self._run(str(tmp_path))
        assert r.returncode == 0

    def test_cli_json_contains_confusable_keys(self, tmp_path):
        skill_dir = tmp_path / "plugins" / "hs" / "skills" / "s2"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: hs:s2\ndescription: Build APIs. Use when starting, scaffolding, or generating.\n---\n",
            encoding="utf-8",
        )
        r = self._run(str(tmp_path / "plugins"), "--json")
        data = json.loads(r.stdout)
        assert "confusable_pairs" in data
