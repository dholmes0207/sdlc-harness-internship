"""test_portback_p5.py — restored reference libraries (thinking frameworks,
metric commands, worked examples, configuration guides).

Pure-prose ports; the suite locks presence + the load-bearing markers that prove
the content (not just an empty stub) landed, and that the re-brand left no ck:
brand or dead-tree path. Markers chosen to be non-vacuous: each was absent before
the port and present in the ck source.
"""
import re
from pathlib import Path

import pytest  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]
_S = _REPO / "harness" / "plugins" / "hs" / "skills"

_NEW = [
    _S / "brainstorm" / "references" / "problem-first.md",
    _S / "brainstorm" / "references" / "editorial-magazine-html.md",
    _S / "plan" / "references" / "archive-workflow.md",
    _S / "plan" / "references" / "solution-design.md",
    _S / "debug" / "references" / "frontend-verification.md",
    _S / "debug" / "references" / "performance-diagnostics.md",
    _S / "sequential-thinking" / "references" / "examples-api.md",
    _S / "sequential-thinking" / "references" / "examples-debug.md",
    _S / "sequential-thinking" / "references" / "examples-architecture.md",
    _S / "mermaidjs" / "references" / "configuration.md",
    _S / "web-testing" / "references" / "ui-testing-workflow.md",
]


@pytest.mark.dev_repo
def test_new_files_exist():
    missing = [str(p.relative_to(_REPO)) for p in _NEW if not p.exists()]
    assert not missing, "missing:\n" + "\n".join(missing)


def test_loop_metric_library_is_multistack():
    body = (_S / "loop" / "references" / "metric-presets.md").read_text(encoding="utf-8")
    # was Python-only; the full library adds non-Python stacks
    assert "go test" in body, "loop metric library still Python-only (no Go stack)"


@pytest.mark.dev_repo
def test_port_has_head_to_head_template():
    body = (_S / "port" / "SKILL.md").read_text(encoding="utf-8")
    assert "Head-to-Head" in body, "port --compare missing Head-to-Head template"


def test_perf_diagnostics_has_postgres_toolkit():
    body = (_S / "debug" / "references" / "performance-diagnostics.md").read_text(encoding="utf-8")
    assert "EXPLAIN ANALYZE" in body, "performance-diagnostics missing the PostgreSQL toolkit"


def test_research_depth_modes_has_deprecation_heuristic():
    body = (_S / "research" / "references" / "depth-modes.md").read_text(encoding="utf-8")
    assert "deprecation" in body.lower(), "research depth-modes missing CVE/deprecation heuristic"


def test_brainstorm_skill_mentions_html():
    body = (_S / "brainstorm" / "SKILL.md").read_text(encoding="utf-8")
    assert "--html" in body, "brainstorm SKILL missing --html mention"


_BAD = [re.compile(r"/?\bck:"), re.compile(re.escape("." + "claude" + "/") + r"(skills|hooks)/")]


def test_ported_set_rebranded_clean():
    offenders = []
    for p in _NEW:
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("# learn:"):
                continue
            for pat in _BAD:
                if pat.search(line):
                    offenders.append(f"{p.relative_to(_REPO)}:{i}: {line.strip()[:80]}")
    assert not offenders, "brand/dead-path survived:\n" + "\n".join(offenders)
