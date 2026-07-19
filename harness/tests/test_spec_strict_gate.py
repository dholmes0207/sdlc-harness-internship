"""hs:spec — strict_gate.py: the shell-runnable CI door.

Exit 0 on a clean spec, exit 2 on any error-severity finding — usable from a
shell pipeline without an LLM. Also covers the conditional cross-layer check
that reads a BA shape task's `serves[]` (as DATA, never by importing shape's
serves_resolver — a one-way layering rule) and flags a dangling story
reference.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402
from conftest import make_proj  # noqa: E402

_NAMES = [
    "encoding_utils", "id_grammar", "frontmatter_parser", "spec_graph", "dec_ledger",
    "check_consistency_schema", "check_consistency_time", "check_consistency_risk",
    "check_consistency_competition", "session_staleness", "check_consistency_product",
    "check_consistency", "check_traceability", "strict_gate",
]
_mods = load_skill_scripts(_SPEC_SCRIPTS, _NAMES)
strict_gate = _mods["strict_gate"]


def _run(monkeypatch, proj):
    monkeypatch.setattr(sys, "argv", ["strict_gate.py", "--root", str(proj)])
    return strict_gate.main()


def test_strict_gate_clean_tree_exits_0(tmp_path, monkeypatch, capsys):
    proj = make_proj(tmp_path)
    rc = _run(monkeypatch, proj)
    err = capsys.readouterr().err
    assert rc == strict_gate.EXIT_OK
    assert "0 errors" in err


def test_strict_gate_error_tree_exits_2(tmp_path, monkeypatch, capsys):
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    story.write_text(
        story.read_text(encoding="utf-8").replace("epic: PRD-AUTH-E1\n", ""),
        encoding="utf-8",
    )
    rc = _run(monkeypatch, proj)
    err = capsys.readouterr().err
    assert rc == strict_gate.EXIT_BLOCKED
    assert "BLOCKED on errors" in err
    assert "orphan_story" in err


# ---------------------------------------------------------------------------
# Shape-serves cross-layer check — orphaned `serves` (a BA shape task pointing
# at a dead story) -> exit 2
# ---------------------------------------------------------------------------

def _write_shape_task(proj: Path, task_id: str, serves: list) -> Path:
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    serves_yaml = "[" + ", ".join(serves) + "]"
    path = tasks_dir / f"{task_id}.md"
    path.write_text(
        f"""---
id: {task_id}
type: task
serves: {serves_yaml}
status: draft
---

# {task_id}
""",
        encoding="utf-8",
    )
    return path


def test_strict_gate_orphaned_serves_exits_2(tmp_path, monkeypatch, capsys):
    proj = make_proj(tmp_path)
    # Delete the spec story a shape task will claim to serve.
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    story.unlink()
    _write_shape_task(proj, "TASK-1", ["PRD-AUTH-E1-S1"])

    rc = _run(monkeypatch, proj)
    err = capsys.readouterr().err
    assert rc == strict_gate.EXIT_BLOCKED
    assert "dangling_serves" in err


def test_strict_gate_shape_absent_is_a_noop(tmp_path, monkeypatch, capsys):
    proj = make_proj(tmp_path)
    assert not (proj / "docs" / "product" / "shape").exists()
    findings, graph = strict_gate.collect_findings(proj)
    assert not any(f["check"] == "dangling_serves" for f in findings)
    rc = _run(monkeypatch, proj)
    capsys.readouterr()
    assert rc == strict_gate.EXIT_OK


def test_strict_gate_serves_resolves_no_finding(tmp_path, monkeypatch, capsys):
    # A shape task serving a LIVE story must not be flagged.
    proj = make_proj(tmp_path)
    _write_shape_task(proj, "TASK-1", ["PRD-AUTH-E1-S1"])
    findings, graph = strict_gate.collect_findings(proj)
    assert not any(f["check"] == "dangling_serves" for f in findings)
    rc = _run(monkeypatch, proj)
    capsys.readouterr()
    assert rc == strict_gate.EXIT_OK


def test_strict_gate_non_string_serves_entry_is_flagged_not_skipped(tmp_path, monkeypatch, capsys):
    # A non-string `serves` entry (e.g. hand-edited `serves: [2, null, 3.5]`)
    # must not silently `continue` past every entry — that reports "0 errors"
    # / exit 0 while the ids are actually dangling (no live story resolves a
    # bare int/null/float). Must exit 2 with a finding.
    proj = make_proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        """---
id: TASK-1
type: task
serves: [2, null, 3.5]
status: draft
---

# TASK-1
""",
        encoding="utf-8",
    )
    rc = _run(monkeypatch, proj)
    err = capsys.readouterr().err
    assert rc == strict_gate.EXIT_BLOCKED
    assert "dangling_serves" in err


def test_strict_gate_scalar_serves_is_flagged_not_skipped(tmp_path, monkeypatch, capsys):
    # A `serves` that is a bare scalar (not a list at all — e.g. a hand-edit
    # `serves: PRD-AUTH-E1-S1` without brackets) must not silently `continue`
    # past the whole task via the isinstance(list) guard — that let an orphan
    # task pass the gate clean. Must exit 2 with a finding.
    proj = make_proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        """---
id: TASK-1
type: task
serves: PRD-AUTH-E1-S1
status: draft
---

# TASK-1
""",
        encoding="utf-8",
    )
    rc = _run(monkeypatch, proj)
    err = capsys.readouterr().err
    assert rc == strict_gate.EXIT_BLOCKED
    assert "dangling_serves" in err


def test_strict_gate_absent_serves_is_still_a_noop(tmp_path, monkeypatch, capsys):
    # A task with no `serves` key at all stays a no-op — only a PRESENT but
    # non-list `serves` is flagged.
    proj = make_proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        """---
id: TASK-1
type: task
status: draft
---

# TASK-1
""",
        encoding="utf-8",
    )
    findings, graph = strict_gate.collect_findings(proj)
    assert not any(f["check"] == "dangling_serves" for f in findings)
    rc = _run(monkeypatch, proj)
    capsys.readouterr()
    assert rc == strict_gate.EXIT_OK


def test_strict_gate_empty_serves_list_is_still_a_noop(tmp_path, monkeypatch, capsys):
    # `serves: []` normalizes to ([], []) via id_grammar.normalize_serves — a
    # shape task not yet wired to any story is a valid draft state, not an
    # error. Only a serves id that FAILS to resolve is a defect.
    proj = make_proj(tmp_path)
    _write_shape_task(proj, "TASK-1", [])
    findings, graph = strict_gate.collect_findings(proj)
    assert not any(f["check"] == "dangling_serves" for f in findings)
    rc = _run(monkeypatch, proj)
    capsys.readouterr()
    assert rc == strict_gate.EXIT_OK


def test_strict_gate_reads_data_not_import(tmp_path):
    # Layering guard (one-way layering): strict_gate must never IMPORT
    # shape's serves_resolver (the docstring may still discuss it in prose,
    # explaining the deliberate data-only choice) — it reads task frontmatter
    # as DATA only.
    src = (_SPEC_SCRIPTS / "strict_gate.py").read_text(encoding="utf-8")
    assert "import serves_resolver" not in src
    assert "from serves_resolver" not in src
    assert "import shape" not in src


def test_strict_gate_docstring_has_no_dotclaude_skills_invoke_example():
    # An earlier draft of this docstring's CI-example carried the banned
    # dot-claude skills literal; this repo's own skill-local invocation
    # replaced it.
    # learn: CI hard-constraint #1 — the assertion below cites the banned literal as data.
    src = (_SPEC_SCRIPTS / "strict_gate.py").read_text(encoding="utf-8")
    assert ".claude/skills/" not in src  # learn: CI hard-constraint #1 banned literal
    assert "harness/plugins/hs/skills/spec/scripts/strict_gate.py" in src


def test_strict_gate_orders_task_files_numerically_not_lexicographically(tmp_path, monkeypatch, capsys):
    # A raw sorted(tasks_dir.glob("*.md")) sorts lexicographically -- TASK-10
    # before TASK-2 -- diverging from task_model.py's numeric sort. Both
    # dangling here so the findings list preserves glob-derived processing
    # order; it must come out TASK-2 before TASK-10.
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    story.unlink()
    _write_shape_task(proj, "TASK-2", ["PRD-AUTH-E1-S1"])
    _write_shape_task(proj, "TASK-10", ["PRD-AUTH-E1-S1"])

    findings, _graph = strict_gate.collect_findings(proj)
    dangling = [f for f in findings if f["check"] == "dangling_serves"]
    task_ids_in_order = [f["artifact_id"] for f in dangling]
    assert task_ids_in_order == ["TASK-2", "TASK-10"]


def test_check_shape_serves_non_string_id_carrier_is_stringified(tmp_path):
    # A hand-edited `id: [TASK-1, TASK-2]` (truthy non-string) must not leak a
    # raw Python list into artifact_id -- the carrier stays a string (filename
    # stem), matching the str-or-None invariant every other finding site holds.
    # The task is still SURFACED (validator role): only the carrier is normalized.
    proj = make_proj(tmp_path)
    story = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    story.unlink()
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        "---\nid: [TASK-1, TASK-2]\nserves: [PRD-AUTH-E1-S1]\n---\n\nbad\n",
        encoding="utf-8",
    )
    findings, _g = strict_gate.collect_findings(proj)
    dangling = [f for f in findings if f["check"] == "dangling_serves"]
    assert dangling, "malformed-id task must still be surfaced by the gate"
    assert all(isinstance(f["artifact_id"], str) for f in dangling)
    assert dangling[0]["artifact_id"] == "TASK-1"  # filename stem, not the raw list


def test_check_shape_serves_dedupes_duplicate_serves(tmp_path):
    # `serves: [S, S]` (hand-edit) is ONE dangling edge, not two findings --
    # mirrors serves_resolver.resolve_serves + spec_graph dedupe.
    proj = make_proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: [STORY-GHOST, STORY-GHOST]\n---\n\nx\n",
        encoding="utf-8",
    )
    findings, _g = strict_gate.collect_findings(proj)
    dangling = [f for f in findings if f["check"] == "dangling_serves"]
    assert len(dangling) == 1


def test_sorted_task_files_deterministic_tiebreak_on_duplicate_numeric_id(tmp_path):
    # Two files colliding on the SAME numeric id (TASK-2.md + TASK-02.md) must
    # order deterministically -- task_model/serves_resolver sort (int, Path)
    # tuples, so a numeric tie breaks on the path, not on filesystem glob order.
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "TASK-2.md").write_text("---\nid: TASK-2\n---\n", encoding="utf-8")
    (tasks_dir / "TASK-02.md").write_text("---\nid: TASK-2\n---\n", encoding="utf-8")
    order = [p.name for p in strict_gate._sorted_task_files(tasks_dir)]
    assert order == ["TASK-02.md", "TASK-2.md"]


def test_strict_gate_ignores_stray_non_task_file_in_tasks_dir(tmp_path, monkeypatch, capsys):
    # task_model.list_tasks() / serves_resolver.list_task_records() both
    # require the TASK-<n>.md grammar and never count a non-matching file as
    # a task -- the gate must agree, not block over a file the BA tooling
    # itself ignores.
    proj = make_proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "backlog-notes.md").write_text(
        "---\nserves: STORY-999\n---\n\n# notes\n", encoding="utf-8",
    )
    findings, _graph = strict_gate.collect_findings(proj)
    assert not any(f["check"] == "dangling_serves" for f in findings)
    rc = _run(monkeypatch, proj)
    capsys.readouterr()
    assert rc == strict_gate.EXIT_OK


def test_strict_gate_blocks_on_empty_workspace(tmp_path):
    # A gate keyed on exit code must not report green having checked zero
    # artifacts (wrong --root / no spec yet). Blocks unless --allow-empty.
    (tmp_path / "docs" / "product").mkdir(parents=True)
    import subprocess, sys
    script = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts" / "strict_gate.py"
    blocked = subprocess.run([sys.executable, str(script), "--root", str(tmp_path)], capture_output=True, text=True)
    assert blocked.returncode == 2
    allowed = subprocess.run([sys.executable, str(script), "--root", str(tmp_path), "--allow-empty"], capture_output=True, text=True)
    assert allowed.returncode == 0


def test_strict_gate_duplicate_task_id_flagged(tmp_path, monkeypatch, capsys):
    # Two DISTINCT task files sharing the same frontmatter id (copy TASK-1.md to
    # TASK-2.md, forget to bump the inner id) -> dup_task_id error, mirroring
    # spec's dup_id for PO artifacts. Left unflagged it makes serves_resolver's
    # two coverage maps silently disagree.
    proj = make_proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("TASK-1.md", "TASK-2.md"):
        (tasks_dir / fname).write_text(
            "---\nid: TASK-1\ntype: task\nserves: [PRD-AUTH-E1-S1]\nstatus: draft\n---\n\n# t\n",
            encoding="utf-8")
    findings, graph = strict_gate.collect_findings(proj)
    assert any(f["check"] == "dup_task_id" for f in findings)
    rc = _run(monkeypatch, proj)
    capsys.readouterr()
    assert rc == strict_gate.EXIT_BLOCKED
