"""hs:shape — epic/prd selector fan-out for `--task`.

`--task` may name an epic/prd container. The container is only a SELECTOR: it
fans out to the descendant stories so a dev task can be authored for each, but
every authored task's `serves` still points at a STORY id, never at the
container. The scope choice (approved-only vs include-draft) and the
empty-branch route back to hs:spec are exercised here; the PO story tree is
never mutated by any of it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SHAPE_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "shape" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402
from conftest import make_proj  # noqa: E402

_mods = load_skill_scripts(
    _SHAPE_SCRIPTS,
    ["shape_paths", "task_model", "serves_resolver", "descendant_resolver", "task_selector"],
)
task_model = _mods["task_model"]
task_selector = _mods["task_selector"]

_PRD = "PRD-AUTH"
_EPIC1 = "PRD-AUTH-E1"
_STORY_1 = "PRD-AUTH-E1-S1"  # draft in the VALID fixture


def _proj(tmp_path: Path) -> Path:
    return make_proj(tmp_path, git=False)


def _write_story(proj: Path, story_id: str, epic: str, status: str) -> None:
    (proj / "docs" / "product" / "stories" / (story_id + ".md")).write_text(
        "---\n"
        "id: %s\ntype: story\nepic: %s\nstatus: %s\nlang: en\n"
        "owner: Jane Doe\nversion: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n"
        "personas: [shopper]\nscope: in\nmoscow: must\nsize: S\nhorizon: now\n"
        "---\n\n# Story %s\n" % (story_id, epic, status, story_id),
        encoding="utf-8",
    )


def _write_epic(proj: Path, epic_id: str, prd: str, status: str = "approved") -> None:
    (proj / "docs" / "product" / "epics" / (epic_id + ".md")).write_text(
        "---\n"
        "id: %s\ntype: epic\nprd: %s\nbrd_goals: [BRD-G1]\nstatus: %s\nlang: en\n"
        "owner: Jane Doe\nversion: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n"
        "personas: [shopper]\nscope: in\nmoscow: must\nhorizon: now\n"
        "---\n\n# Epic %s\n" % (epic_id, prd, status, epic_id),
        encoding="utf-8",
    )


def _po_subtree_bytes(proj: Path) -> dict:
    """Snapshot the PO-owned subtrees (never the BA shape/ sidecar)."""
    out = {}
    for sub in ("stories", "epics", "prds", "vision.md", "brd.md"):
        base = proj / "docs" / "product" / sub
        if base.is_dir():
            for p in base.rglob("*"):
                if p.is_file():
                    out[p.relative_to(proj).as_posix()] = p.read_bytes()
        elif base.is_file():
            out[base.relative_to(proj).as_posix()] = base.read_bytes()
    return out


# ---------------------------------------------------------------------------
# task_model.author gains an optional from_draft mark (additive, back-compat)
# ---------------------------------------------------------------------------

def test_author_from_draft_writes_field(tmp_path):
    proj = _proj(tmp_path)
    rec = task_model.author(proj, serves=[_STORY_1], title="t", from_draft=True)
    assert rec["from_draft"] is True
    on_disk = (proj / "docs" / "product" / "shape" / "tasks" / "TASK-1.md").read_text(
        encoding="utf-8"
    )
    assert "from_draft: true" in on_disk


def test_author_default_omits_from_draft_field(tmp_path):
    # The common (approved / explicit-story) path keeps the old record shape.
    proj = _proj(tmp_path)
    rec = task_model.author(proj, serves=[_STORY_1], title="t")
    assert "from_draft" not in rec


def test_author_from_draft_record_schema_valid(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    proj = _proj(tmp_path)
    rec = task_model.author(proj, serves=[_STORY_1], from_draft=True)
    schema = json.loads(
        (_SHAPE_SCRIPTS.parent / "schemas" / "task.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(instance=rec, schema=schema)


# ---------------------------------------------------------------------------
# epic/prd selector fan-out
# ---------------------------------------------------------------------------

def test_selector_epic_approved_only_skips_draft(tmp_path):
    proj = _proj(tmp_path)  # _STORY_1 is draft
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    result = task_selector.author_tasks_for_selector(proj, _EPIC1, include_draft=False)
    assert result["empty_branch"] is False
    served = {sid for rec in result["authored"] for sid in rec["serves"]}
    assert served == {"PRD-AUTH-E1-S2"}
    assert result["skipped_draft"] == [_STORY_1]
    # nothing authored from an approved story carries the from_draft mark
    assert all("from_draft" not in rec for rec in result["authored"])


def test_selector_epic_include_draft_marks_from_draft(tmp_path):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    result = task_selector.author_tasks_for_selector(proj, _EPIC1, include_draft=True)
    by_story = {rec["serves"][0]: rec for rec in result["authored"]}
    assert set(by_story) == {_STORY_1, "PRD-AUTH-E1-S2"}
    assert by_story[_STORY_1].get("from_draft") is True          # draft
    assert "from_draft" not in by_story["PRD-AUTH-E1-S2"]        # approved
    assert result["skipped_draft"] == []


def test_selector_serves_points_at_story_never_container(tmp_path):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    result = task_selector.author_tasks_for_selector(proj, _EPIC1, include_draft=True)
    for rec in result["authored"]:
        assert rec["serves"], "a task must serve at least one story"
        assert _EPIC1 not in rec["serves"]
        assert _PRD not in rec["serves"]
        assert all(sid.startswith("PRD-AUTH-E1-S") for sid in rec["serves"])


def test_selector_epic_all_draft_approved_only_authors_nothing(tmp_path):
    # Every child story is draft + approved-only: authored=[], all skipped, but
    # this is NOT an empty branch (there ARE stories, just none approved).
    proj = _proj(tmp_path)  # _STORY_1 is draft
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "draft")
    result = task_selector.author_tasks_for_selector(proj, _EPIC1, include_draft=False)
    assert result["empty_branch"] is False
    assert result["authored"] == []
    assert set(result["skipped_draft"]) == {_STORY_1, "PRD-AUTH-E1-S2"}


def test_selector_prd_unions_across_epics(tmp_path):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S1b", _EPIC1, "approved")
    _write_epic(proj, "PRD-AUTH-E2", _PRD, "approved")
    _write_story(proj, "PRD-AUTH-E2-S1", "PRD-AUTH-E2", "approved")
    result = task_selector.author_tasks_for_selector(proj, _PRD, include_draft=False)
    served = {sid for rec in result["authored"] for sid in rec["serves"]}
    assert served == {"PRD-AUTH-E1-S1b", "PRD-AUTH-E2-S1"}


# ---------------------------------------------------------------------------
# Empty branch: HARD STOP + route, never authors, never touches PO tree
# ---------------------------------------------------------------------------

def test_selector_empty_epic_routes_and_authors_nothing(tmp_path):
    proj = _proj(tmp_path)
    _write_epic(proj, "PRD-AUTH-E3", _PRD, "approved")  # no child stories
    result = task_selector.author_tasks_for_selector(proj, "PRD-AUTH-E3")
    assert result["empty_branch"] is True
    assert result["authored"] == []
    assert "hs:spec --story" in result["route"]
    assert "PRD-AUTH-E3" in result["route"]
    # no task file was written
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    assert not tasks_dir.exists() or not list(tasks_dir.glob("TASK-*.md"))


# ---------------------------------------------------------------------------
# Story target keeps the old path (author directly, no scope filter/mark)
# ---------------------------------------------------------------------------

def test_selector_story_target_authors_directly(tmp_path):
    proj = _proj(tmp_path)  # _STORY_1 is draft, but an explicit story is authored as-is
    result = task_selector.author_tasks_for_selector(proj, _STORY_1)
    assert result["target_kind"] == "story"
    assert result["empty_branch"] is False
    assert len(result["authored"]) == 1
    assert result["authored"][0]["serves"] == [_STORY_1]
    assert "from_draft" not in result["authored"][0]


# ---------------------------------------------------------------------------
# The PO story tree is never mutated by a selector author
# ---------------------------------------------------------------------------

def test_selector_does_not_mutate_po_tree(tmp_path):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    before = _po_subtree_bytes(proj)
    task_selector.author_tasks_for_selector(proj, _EPIC1, include_draft=True)
    after = _po_subtree_bytes(proj)
    assert after == before


# ---------------------------------------------------------------------------
# CLI: author via `--root --target [--include-draft]`, JSON out
# ---------------------------------------------------------------------------

def test_cli_authors_and_outputs_json(tmp_path, capsys):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    rc = task_selector.main(["--root", str(proj), "--target", _EPIC1])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["empty_branch"] is False
    served = {sid for rec in out["authored"] for sid in rec["serves"]}
    assert served == {"PRD-AUTH-E1-S2"}          # approved-only default
    assert out["skipped_draft"] == [_STORY_1]


def test_cli_include_draft_flag(tmp_path, capsys):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    rc = task_selector.main(
        ["--root", str(proj), "--target", _EPIC1, "--include-draft"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    served = {sid for rec in out["authored"] for sid in rec["serves"]}
    assert served == {_STORY_1, "PRD-AUTH-E1-S2"}
