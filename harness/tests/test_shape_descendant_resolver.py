"""hs:shape — descendant-resolver (the epic/prd selector core).

The `--task` selector may name an epic/prd container, not just a story. This
resolver fans a container id out to its child STORY nodes and classifies each
(status + whether a dev task already serves it), reading only the existing PO
spec graph and the existing serves map — never hand-parsing frontmatter, never
writing, never touching the PO tree. A task's `serves` still only ever points
at a story, so the container is a selector, never a serve target.

Guards below pin two easy-to-miss rules: a non-story node hand-edited to carry
`epic: <target>` must NOT leak into the story list, and an empty branch is
defined by story COUNT (a prd with child epics but zero stories is still empty).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SHAPE_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "shape" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402
from conftest import make_proj  # noqa: E402

_mods = load_skill_scripts(
    _SHAPE_SCRIPTS, ["shape_paths", "task_model", "serves_resolver", "descendant_resolver"]
)
descendant_resolver = _mods["descendant_resolver"]
task_model = _mods["task_model"]

_PRD = "PRD-AUTH"
_EPIC1 = "PRD-AUTH-E1"
_STORY_1 = "PRD-AUTH-E1-S1"  # draft, from the VALID fixture


def _proj(tmp_path: Path) -> Path:
    return make_proj(tmp_path, git=False)


def _write_story(proj: Path, story_id: str, epic: str, status: str) -> None:
    (proj / "docs" / "product" / "stories" / (story_id + ".md")).write_text(
        "---\n"
        "id: %s\n"
        "type: story\n"
        "epic: %s\n"
        "status: %s\n"
        "lang: en\n"
        "owner: Jane Doe\n"
        "version: 0.1.0\n"
        "created: 2026-05-28\n"
        "updated: 2026-05-28\n"
        "personas: [shopper]\n"
        "scope: in\n"
        "moscow: must\n"
        "size: S\n"
        "horizon: now\n"
        "---\n\n# Story %s\n" % (story_id, epic, status, story_id),
        encoding="utf-8",
    )


def _write_epic(proj: Path, epic_id: str, prd: str, status: str = "approved") -> None:
    (proj / "docs" / "product" / "epics" / (epic_id + ".md")).write_text(
        "---\n"
        "id: %s\n"
        "type: epic\n"
        "prd: %s\n"
        "brd_goals: [BRD-G1]\n"
        "status: %s\n"
        "lang: en\n"
        "owner: Jane Doe\n"
        "version: 0.1.0\n"
        "created: 2026-05-28\n"
        "updated: 2026-05-28\n"
        "personas: [shopper]\n"
        "scope: in\n"
        "moscow: must\n"
        "horizon: now\n"
        "---\n\n# Epic %s\n" % (epic_id, prd, status, epic_id),
        encoding="utf-8",
    )


def _by_id(result) -> dict:
    return {s["id"]: s for s in result["stories"]}


# ---------------------------------------------------------------------------
# Epic target: classify child stories (status + has_task)
# ---------------------------------------------------------------------------

def test_epic_target_returns_child_stories_with_status(tmp_path):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    result = descendant_resolver.resolve_descendant_stories(proj, _EPIC1)
    assert result["target_kind"] == "epic"
    assert result["empty_branch"] is False
    by_id = _by_id(result)
    assert set(by_id) == {_STORY_1, "PRD-AUTH-E1-S2"}
    assert by_id[_STORY_1]["status"] == "draft"
    assert by_id["PRD-AUTH-E1-S2"]["status"] == "approved"


def test_epic_target_marks_has_task(tmp_path):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    task_model.author(proj, serves=[_STORY_1], title="build sign-in")
    result = descendant_resolver.resolve_descendant_stories(proj, _EPIC1)
    by_id = _by_id(result)
    assert by_id[_STORY_1]["has_task"] is True
    assert by_id["PRD-AUTH-E1-S2"]["has_task"] is False


# ---------------------------------------------------------------------------
# PRD target: union across multiple child epics
# ---------------------------------------------------------------------------

def test_prd_target_unions_stories_across_epics(tmp_path):
    proj = _proj(tmp_path)
    _write_epic(proj, "PRD-AUTH-E2", _PRD, "approved")
    _write_story(proj, "PRD-AUTH-E2-S1", "PRD-AUTH-E2", "approved")
    result = descendant_resolver.resolve_descendant_stories(proj, _PRD)
    assert result["target_kind"] == "prd"
    assert result["empty_branch"] is False
    assert set(_by_id(result)) == {_STORY_1, "PRD-AUTH-E2-S1"}


# ---------------------------------------------------------------------------
# Empty branches (defined by STORY count)
# ---------------------------------------------------------------------------

def test_empty_epic_is_empty_branch(tmp_path):
    proj = _proj(tmp_path)
    _write_epic(proj, "PRD-AUTH-E3", _PRD, "approved")  # no child stories
    result = descendant_resolver.resolve_descendant_stories(proj, "PRD-AUTH-E3")
    assert result["target_kind"] == "epic"
    assert result["stories"] == []
    assert result["empty_branch"] is True


def test_prd_with_epics_but_zero_stories_is_empty_branch(tmp_path):
    # empty_branch is defined by story COUNT, not by "has child epics".
    proj = _proj(tmp_path)
    # Remove the only story so PRD-AUTH keeps its epic but has zero stories.
    (proj / "docs" / "product" / "stories" / (_STORY_1 + ".md")).unlink()
    _write_epic(proj, "PRD-AUTH-E9", _PRD, "approved")  # extra empty epic
    result = descendant_resolver.resolve_descendant_stories(proj, _PRD)
    assert result["target_kind"] == "prd"
    assert result["stories"] == []
    assert result["empty_branch"] is True


# ---------------------------------------------------------------------------
# Story target returns itself; unknown id is empty
# ---------------------------------------------------------------------------

def test_story_target_returns_itself(tmp_path):
    proj = _proj(tmp_path)
    result = descendant_resolver.resolve_descendant_stories(proj, _STORY_1)
    assert result["target_kind"] == "story"
    assert result["empty_branch"] is False
    assert [s["id"] for s in result["stories"]] == [_STORY_1]


def test_unknown_id_is_empty_branch_none_kind(tmp_path):
    proj = _proj(tmp_path)
    result = descendant_resolver.resolve_descendant_stories(proj, "PRD-GHOST-E9-S9")
    assert result["target_kind"] is None
    assert result["stories"] == []
    assert result["empty_branch"] is True


# ---------------------------------------------------------------------------
# A non-story node carrying epic==target must NOT leak into stories
# ---------------------------------------------------------------------------

def test_non_story_node_with_epic_link_is_excluded(tmp_path):
    proj = _proj(tmp_path)
    # A hand-edited nested epic that wrongly carries `epic: PRD-AUTH-E1`.
    # It is type=epic, so it must never be counted as a child STORY of E1.
    (proj / "docs" / "product" / "epics" / "PRD-AUTH-E1-NESTED.md").write_text(
        "---\n"
        "id: PRD-AUTH-E1-NESTED\n"
        "type: epic\n"
        "prd: PRD-AUTH\n"
        "epic: PRD-AUTH-E1\n"
        "brd_goals: [BRD-G1]\n"
        "status: approved\n"
        "lang: en\n"
        "owner: Jane Doe\n"
        "version: 0.1.0\n"
        "created: 2026-05-28\n"
        "updated: 2026-05-28\n"
        "personas: [shopper]\n"
        "scope: in\n"
        "moscow: must\n"
        "horizon: now\n"
        "---\n\n# Nested\n",
        encoding="utf-8",
    )
    result = descendant_resolver.resolve_descendant_stories(proj, _EPIC1)
    ids = {s["id"] for s in result["stories"]}
    assert "PRD-AUTH-E1-NESTED" not in ids
    assert _STORY_1 in ids


# ---------------------------------------------------------------------------
# Read-only: resolving never mutates the PO tree
# ---------------------------------------------------------------------------

def test_resolver_does_not_touch_po_tree(tmp_path):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    product = proj / "docs" / "product"
    before = {
        p.relative_to(product).as_posix(): p.read_bytes()
        for p in product.rglob("*") if p.is_file()
    }
    descendant_resolver.resolve_descendant_stories(proj, _EPIC1)
    after = {
        p.relative_to(product).as_posix(): p.read_bytes()
        for p in product.rglob("*") if p.is_file()
    }
    assert after == before


# ---------------------------------------------------------------------------
# Fail-soft: a missing product tree returns empty, never raises
# ---------------------------------------------------------------------------

def test_missing_product_tree_returns_empty_not_raise(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    result = descendant_resolver.resolve_descendant_stories(bare, "PRD-AUTH")
    assert result["target_kind"] is None
    assert result["stories"] == []
    assert result["empty_branch"] is True


def test_prd_target_excludes_non_story_node_with_epic_link(tmp_path):
    # The type==story filter must also hold on the prd fan-out path, not only
    # the epic path: a non-story artifact under a child epic must not leak in.
    proj = _proj(tmp_path)
    _write_epic(proj, "PRD-AUTH-E2", _PRD, "approved")
    _write_story(proj, "PRD-AUTH-E2-S1", "PRD-AUTH-E2", "approved")
    (proj / "docs" / "product" / "epics" / "PRD-AUTH-E2-NESTED.md").write_text(
        "---\n"
        "id: PRD-AUTH-E2-NESTED\n"
        "type: epic\n"
        "prd: PRD-AUTH\n"
        "epic: PRD-AUTH-E2\n"
        "brd_goals: [BRD-G1]\n"
        "status: approved\n"
        "lang: en\n"
        "owner: Jane Doe\n"
        "version: 0.1.0\n"
        "created: 2026-05-28\n"
        "updated: 2026-05-28\n"
        "personas: [shopper]\n"
        "scope: in\n"
        "moscow: must\n"
        "horizon: now\n"
        "---\n\n# Nested\n",
        encoding="utf-8",
    )
    result = descendant_resolver.resolve_descendant_stories(proj, _PRD)
    assert "PRD-AUTH-E2-NESTED" not in {s["id"] for s in result["stories"]}
    assert {_STORY_1, "PRD-AUTH-E2-S1"} <= {s["id"] for s in result["stories"]}


def test_non_container_target_is_empty_branch(tmp_path):
    # A target that resolves to a non-container node (a BRD goal / vision) is
    # not a valid selector -> empty stories, empty_branch True, kind preserved.
    proj = _proj(tmp_path)
    result = descendant_resolver.resolve_descendant_stories(proj, "BRD-G1")
    assert result["stories"] == []
    assert result["empty_branch"] is True
    assert result["target_kind"] != "story"


def test_cli_outputs_json(tmp_path, capsys):
    proj = _proj(tmp_path)
    _write_story(proj, "PRD-AUTH-E1-S2", _EPIC1, "approved")
    import json
    rc = descendant_resolver.main(["--root", str(proj), "--target", _EPIC1])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["target_kind"] == "epic"
    assert {s["id"] for s in out["stories"]} == {_STORY_1, "PRD-AUTH-E1-S2"}
