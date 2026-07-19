"""hs:spec authoring: ID allocation, discover ingest, session
resume, SKILL.md frontmatter, and the `.claude/` scrub gate for the
authoring surface (interview banks, workflow refs, guardrails, scripts)."""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Literal path keeps the stashed-skill collect_ignore coupling working:
# harness/plugins/hs/skills/spec/scripts
_SPEC_DIR = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec"
_SPEC_SCRIPTS = _SPEC_DIR / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_mods = load_skill_scripts(_SPEC_SCRIPTS, [
    "encoding_utils", "frontmatter_parser", "id_grammar", "spec_graph",
    "fs_guard", "template_id_alloc", "ingest_raw_inputs",
])
spec_graph = _mods["spec_graph"]
template_id_alloc = _mods["template_id_alloc"]
ingest_raw_inputs = _mods["ingest_raw_inputs"]
frontmatter_parser = _mods["frontmatter_parser"]


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _epic_with_stories(root: Path, story_ns) -> None:
    _write(root, "docs/product/PRODUCT.md",
           "---\nid: PRODUCT\ntype: product\nstatus: draft\nlang: en\n"
           "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# P\n")
    _write(root, "docs/product/prds/x.md",
           "---\nid: PRD-X\ntype: prd\nstatus: draft\nlang: en\n"
           "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# PRD X\n")
    _write(root, "docs/product/epics/PRD-X-E1.md",
           "---\nid: PRD-X-E1\ntype: epic\nprd: PRD-X\nstatus: draft\nlang: en\n"
           "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# Epic\n")
    for n in story_ns:
        _write(root, "docs/product/stories/PRD-X-E1-S%d.md" % n,
               "---\nid: PRD-X-E1-S%d\ntype: story\nepic: PRD-X-E1\nstatus: draft\n"
               "lang: en\nversion: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n"
               "---\n# Story %d\n" % (n, n))


# ---------------------------------------------------------------------------
# alloc ID next-in-parent
# ---------------------------------------------------------------------------

def test_alloc_next_story_in_parent(tmp_path):
    _epic_with_stories(tmp_path, [1, 2])
    graph = spec_graph.build_graph(tmp_path)
    next_id = template_id_alloc.allocate_id(graph, "story", None, "PRD-X-E1", [])
    assert next_id == "PRD-X-E1-S3"


def test_alloc_next_epic_in_parent(tmp_path):
    _write(tmp_path, "docs/product/PRODUCT.md",
           "---\nid: PRODUCT\ntype: product\nstatus: draft\nlang: en\n"
           "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# P\n")
    _write(tmp_path, "docs/product/prds/x.md",
           "---\nid: PRD-X\ntype: prd\nstatus: draft\nlang: en\n"
           "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# PRD X\n")
    _write(tmp_path, "docs/product/epics/PRD-X-E1.md",
           "---\nid: PRD-X-E1\ntype: epic\nprd: PRD-X\nstatus: draft\nlang: en\n"
           "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# Epic\n")
    graph = spec_graph.build_graph(tmp_path)
    next_id = template_id_alloc.allocate_id(graph, "epic", None, "PRD-X", [])
    assert next_id == "PRD-X-E2"


# ---------------------------------------------------------------------------
# batch alloc does not reuse a session_used id
# ---------------------------------------------------------------------------

def test_batch_alloc_skips_session_used(tmp_path):
    _epic_with_stories(tmp_path, [1, 2])
    graph = spec_graph.build_graph(tmp_path)
    # S3 not yet written to disk, but already handed out this session.
    handed_out = ["PRD-X-E1-S3"]
    next_id = template_id_alloc.allocate_id(graph, "story", None, "PRD-X-E1", handed_out)
    assert next_id == "PRD-X-E1-S4"


# ---------------------------------------------------------------------------
# discover ingest size-cap
# ---------------------------------------------------------------------------

def test_discover_ingest_rejects_oversize_file(tmp_path):
    big = tmp_path / "notes.md"
    big.write_text("x" * 200, encoding="utf-8")
    result = ingest_raw_inputs.resolve_inputs([str(big)], tmp_path, max_bytes=100)
    assert result["accepted"] == []
    assert len(result["rejected"]) == 1
    assert "size cap" in result["rejected"][0]["reason"]


def test_discover_ingest_accepts_within_cap(tmp_path):
    ok = tmp_path / "notes.md"
    ok.write_text("small note", encoding="utf-8")
    result = ingest_raw_inputs.resolve_inputs([str(ok)], tmp_path, max_bytes=1000)
    assert result["rejected"] == []
    assert len(result["accepted"]) == 1


def test_discover_ingest_does_not_crash_on_directory_walk(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "raw" / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "raw" / "skip.exe").write_bytes(b"\x00\x01")
    result = ingest_raw_inputs.resolve_inputs([str(tmp_path / "raw")], tmp_path)
    accepted_names = {Path(p).name for p in result["accepted"]}
    assert accepted_names == {"a.md", "b.txt"}


# ---------------------------------------------------------------------------
# session resume — .session.md is generic frontmatter, parsed by
# frontmatter_parser (the resume DECISION is LLM prose in workflow-interview.md;
# this is the mechanical backing: phase + pending[0] must be readable).
# ---------------------------------------------------------------------------

def test_session_resume_reads_phase_and_first_pending(tmp_path):
    session = _write(tmp_path, "docs/product/.session.md",
                      "---\nphase: story\nlang: en\ntarget: PRD-X-E1\n"
                      "answers: {}\npending: [S1-user-story, S1-ac]\n"
                      "created: 2026-05-28T00:00:00Z\nupdated: 2026-05-28T00:00:00Z\n"
                      "---\n\n# notes\n")
    parsed = frontmatter_parser.parse_file(session)
    assert parsed["ok"] is True
    assert parsed["frontmatter"]["phase"] == "story"
    assert parsed["frontmatter"]["pending"][0] == "S1-user-story"


# ---------------------------------------------------------------------------
# no `.claude/` ref left in the .py + .md authoring surface
# ---------------------------------------------------------------------------

# Assembled via %s so this line never itself contains the banned literal
# verbatim (test_bug_class_invariants.py::test_no_reference_to_claudekit_tree
# scans harness/ line-by-line for it, `# learn:`-whitelisted lines excepted).
_CLAUDE_REF_RE = re.compile(r"\.claude/(?:%s|%s)/" % ("skills", "hooks"))

_P2_MD_FILES = [
    "SKILL.md",
    "references/interview-vision.md",
    "references/interview-brd.md",
    "references/interview-prd.md",
    "references/interview-epic.md",
    "references/interview-story.md",
    "references/interview-frameworks.md",
    "references/workflow-interview.md",
    "references/workflow-auto.md",
    "references/workflow-discover.md",
    "references/guardrails-and-boundaries.md",
]

_P2_PY_FILES = [
    "scripts/template_id_alloc.py",
    "scripts/generate_templates.py",
    "scripts/ingest_raw_inputs.py",
    "scripts/open_questions.py",
    "scripts/fs_guard.py",
]


@pytest.mark.parametrize("rel", _P2_MD_FILES + _P2_PY_FILES)
def test_no_dotclaude_refs_in_authored_authoring_files(rel):
    text = (_SPEC_DIR / rel).read_text(encoding="utf-8")
    assert not _CLAUDE_REF_RE.search(text), "%s still carries a .claude/ ref" % rel


# ---------------------------------------------------------------------------
# SKILL.md frontmatter is valid
# ---------------------------------------------------------------------------

def test_skill_md_frontmatter_is_valid():
    parsed = frontmatter_parser.parse_file(_SPEC_DIR / "SKILL.md")
    assert parsed["ok"] is True
    fm = parsed["frontmatter"]
    assert fm["name"] == "hs:spec"
    assert "allowed-tools" in fm
    assert fm["metadata"]["compliance-tier"] == "workflow"
    assert "when_to_use" in fm


def test_skill_md_thin_core_line_budget():
    text = (_SPEC_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) < 300


def test_classify_file_rejects_fifo_no_hang(tmp_path):
    # A FIFO/named-pipe named notes.md passes containment/dotfile/extension/size
    # (size 0) but read_text() on it blocks forever. _classify_file must reject a
    # non-regular file (FIFO/socket/device) so the "hard-fenced read surface"
    # never hangs draft_scaffold.
    import os
    fifo = tmp_path / "notes.md"
    os.mkfifo(fifo)
    reason = ingest_raw_inputs._classify_file(fifo.resolve(), tmp_path.resolve(), 10_000)
    assert reason is not None  # rejected, not accepted (None == accept)
