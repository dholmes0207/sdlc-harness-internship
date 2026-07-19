"""
Tests for the ported docs-seeker deterministic scripts.

Red-before-port property: every test fails if the scripts directory is absent
(asserted explicitly at the top of each test via SCRIPTS_DIR.exists()).
These tests are pure script-driven — they do not read any repo-internal
docs/ledger paths, so @pytest.mark.dev_repo is NOT applied.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).parent.parent
    / "plugins" / "hs" / "skills" / "docs-seeker"
)
SCRIPTS_DIR = SKILL_DIR / "scripts"


# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _require_scripts():
    """Abort test with a clear message if scripts dir was never ported."""
    assert SCRIPTS_DIR.exists(), (
        f"scripts directory not found at {SCRIPTS_DIR} — "
        "port the docs-seeker scripts before running this suite"
    )


def _node(script_rel, *args, env_extra=None, input_text=None):
    """Run `node <script>` and return (returncode, stdout, stderr)."""
    script = SCRIPTS_DIR / script_rel
    assert script.exists(), f"script not found: {script}"
    result = subprocess.run(
        ["node", str(script), *args],
        capture_output=True,
        text=True,
        env=env_extra,
        input=input_text,
    )
    return result.returncode, result.stdout, result.stderr


def _node_e(expression, cwd=None):
    """Run `node -e <expression>` in the scripts dir and return (rc, stdout)."""
    result = subprocess.run(
        ["node", "-e", expression],
        capture_output=True,
        text=True,
        cwd=str(SCRIPTS_DIR) if cwd is None else cwd,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# detect-topic.js
# ---------------------------------------------------------------------------

class TestDetectTopic:

    def test_topic_specific_query_extracts_library_and_topic(self):
        _require_scripts()
        rc, out, _ = _node("detect-topic.js", "Next.js caching strategies")
        assert rc == 0, f"detect-topic.js exited {rc}"
        data = json.loads(out)
        assert data["isTopicSpecific"] is True
        assert data["library"] == "next.js"
        assert data["topic"] == "caching"

    def test_topic_query_how_do_i_use(self):
        _require_scripts()
        rc, out, _ = _node("detect-topic.js", "How do I use date picker in shadcn/ui?")
        assert rc == 0
        data = json.loads(out)
        assert data["isTopicSpecific"] is True
        assert data["topic"] == "date"
        assert data["library"] == "shadcn/ui"

    def test_general_query_returns_not_topic_specific(self):
        _require_scripts()
        rc, out, _ = _node("detect-topic.js", "Documentation for Next.js")
        assert rc == 0
        data = json.loads(out)
        assert data["isTopicSpecific"] is False

    def test_no_args_exits_nonzero(self):
        _require_scripts()
        rc, _, stderr = _node("detect-topic.js")
        assert rc != 0
        assert "Usage" in stderr

    def test_implement_query_captures_topic_and_library(self):
        _require_scripts()
        rc, out, _ = _node("detect-topic.js", "Implement routing in Next.js")
        assert rc == 0
        data = json.loads(out)
        assert data["isTopicSpecific"] is True
        assert "next" in data["library"]


# ---------------------------------------------------------------------------
# fetch-docs.js — URL construction logic (no real network calls)
# ---------------------------------------------------------------------------

class TestFetchDocsUrlConstruction:
    """Exercise buildContext7Url and getUrlVariations without hitting the wire."""


    def test_build_url_github_repo_no_topic(self):
        _require_scripts()
        expr = """
const { buildContext7Url } = require('./fetch-docs');
console.log(buildContext7Url('vercel/next.js'));
"""
        rc, out, _ = _node_e(expr)
        assert rc == 0, f"node -e failed: {out}"
        assert out.strip() == "https://context7.com/vercel/next.js/llms.txt"

    def test_build_url_with_topic_appends_query_param(self):
        _require_scripts()
        expr = """
const { buildContext7Url } = require('./fetch-docs');
console.log(buildContext7Url('vercel/next.js', 'cache'));
"""
        rc, out, _ = _node_e(expr)
        assert rc == 0
        url = out.strip()
        assert url == "https://context7.com/vercel/next.js/llms.txt?topic=cache"

    def test_build_url_shadcn_with_topic(self):
        _require_scripts()
        expr = """
const { buildContext7Url } = require('./fetch-docs');
console.log(buildContext7Url('shadcn-ui/ui', 'date'));
"""
        rc, out, _ = _node_e(expr)
        assert rc == 0
        assert "shadcn-ui/ui" in out
        assert "topic=date" in out

    def test_get_url_variations_topic_first_then_fallback(self):
        _require_scripts()
        expr = """
const { getUrlVariations } = require('./fetch-docs');
getUrlVariations('next.js', 'cache').then(urls => {
  console.log(JSON.stringify(urls));
});
"""
        rc, out, _ = _node_e(expr)
        assert rc == 0
        urls = json.loads(out.strip())
        # At least 2 entries: with-topic and without-topic fallback
        assert len(urls) >= 2
        assert "?topic=cache" in urls[0], "first URL must carry the topic param"
        assert "?topic=" not in urls[1], "second URL must be the no-topic fallback"

    def test_get_url_variations_known_repo_mapping(self):
        """Known libraries like 'astro' must resolve to their correct GitHub org/repo."""
        _require_scripts()
        expr = """
const { getUrlVariations } = require('./fetch-docs');
getUrlVariations('astro', 'routing').then(urls => {
  console.log(JSON.stringify(urls));
});
"""
        rc, out, _ = _node_e(expr)
        assert rc == 0
        urls = json.loads(out.strip())
        assert urls[0] == "https://context7.com/withastro/astro/llms.txt?topic=routing"

    def test_fetch_docs_no_args_exits_nonzero(self):
        _require_scripts()
        rc, _, stderr = _node("fetch-docs.js")
        assert rc != 0
        assert "Usage" in stderr


# ---------------------------------------------------------------------------
# analyze-llms-txt.js
# ---------------------------------------------------------------------------

class TestAnalyzeLlmsTxt:
    SAMPLE = "\n".join([
        "# Documentation",
        "https://docs.example.com/getting-started",
        "https://docs.example.com/guide",
        "# Comment",
        "https://docs.example.com/api-reference",
        "",
        "https://docs.example.com/advanced",
        "",
    ])


    def test_analyze_counts_urls(self):
        _require_scripts()
        rc, out, _ = _node("analyze-llms-txt.js", "-", input_text=self.SAMPLE)
        assert rc == 0, f"analyze-llms-txt.js failed: {out}"
        data = json.loads(out)
        assert data["totalUrls"] == 4

    def test_analyze_includes_grouped_and_distribution(self):
        _require_scripts()
        rc, out, _ = _node("analyze-llms-txt.js", "-", input_text=self.SAMPLE)
        assert rc == 0
        data = json.loads(out)
        assert "grouped" in data
        assert "distribution" in data
        assert "summary" in data

    def test_getting_started_is_critical(self):
        _require_scripts()
        content = "https://docs.example.com/getting-started\n"
        rc, out, _ = _node("analyze-llms-txt.js", "-", input_text=content)
        assert rc == 0
        data = json.loads(out)
        assert len(data["grouped"]["critical"]) >= 1

    def test_advanced_is_supplementary(self):
        _require_scripts()
        content = "https://docs.example.com/advanced/internals\n"
        rc, out, _ = _node("analyze-llms-txt.js", "-", input_text=content)
        assert rc == 0
        data = json.loads(out)
        assert len(data["grouped"]["supplementary"]) >= 1

    def test_single_agent_for_few_urls(self):
        """2 URLs → strategy=single, agentCount=1."""
        _require_scripts()
        content = (
            "https://docs.example.com/getting-started\n"
            "https://docs.example.com/guide\n"
        )
        rc, out, _ = _node("analyze-llms-txt.js", "-", input_text=content)
        assert rc == 0
        data = json.loads(out)
        assert data["distribution"]["agentCount"] == 1
        assert data["distribution"]["strategy"] == "single"

    def test_phased_strategy_for_large_url_set(self):
        """25 URLs → strategy=phased."""
        _require_scripts()
        lines = [f"https://docs.example.com/page-{i}" for i in range(25)]
        content = "\n".join(lines) + "\n"
        rc, out, _ = _node("analyze-llms-txt.js", "-", input_text=content)
        assert rc == 0
        data = json.loads(out)
        assert data["distribution"]["strategy"] == "phased"

    def test_no_file_arg_exits_nonzero(self):
        _require_scripts()
        rc, _, stderr = _node("analyze-llms-txt.js")
        assert rc != 0
        assert "Usage" in stderr
