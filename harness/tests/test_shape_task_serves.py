"""hs:shape — dev-task model + serves resolver (BA sidecar).

The BA sidecar (`docs/product/shape/`) is a bridge from an approved PO story
to dev work. It reads the PO story graph but never mutates a PO artifact.
`task_model.py` writes `serves:[story_ids]` task records supporting all
three cardinalities (1-1/1-n/n-1) with no schema special-case;
`serves_resolver.py` resolves those ids against the PO graph and flags a
dangling one. `shape_paths.py` is the canonical script-path containment
helper both writers go through -- see test_shape_path_escape_raises /
test_stories_byte_unchanged_after_task_write below, which are hard guards on
that boundary, not just documentation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Literal path kept intact for the stashed-skill collect_ignore coupling:
# harness/plugins/hs/skills/shape/scripts
_SHAPE_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "shape" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402
from conftest import make_proj  # noqa: E402

_mods = load_skill_scripts(_SHAPE_SCRIPTS, ["shape_paths", "task_model", "serves_resolver"])
shape_paths = _mods["shape_paths"]
task_model = _mods["task_model"]
serves_resolver = _mods["serves_resolver"]

_STORY_1 = "PRD-AUTH-E1-S1"  # from conftest.VALID's fixture tree


def _proj(tmp_path: Path) -> Path:
    return make_proj(tmp_path, git=False)


def _add_second_story(proj: Path) -> str:
    """Add a second story under the same epic so an n-1 task has two real
    story ids to serve."""
    story_id = "PRD-AUTH-E1-S2"
    (proj / "docs" / "product" / "stories" / (story_id + ".md")).write_text(
        "---\n"
        "id: %s\n"
        "type: story\n"
        "epic: PRD-AUTH-E1\n"
        "status: draft\n"
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
        "---\n\n"
        "# Sign-Up Story\n"
        % story_id,
        encoding="utf-8",
    )
    return story_id


# ---------------------------------------------------------------------------
# Containment: shape_path() escape raises
# ---------------------------------------------------------------------------

def test_shape_path_escape_raises(tmp_path):
    with pytest.raises(PermissionError):
        shape_paths.shape_path(tmp_path, "../stories/x.md")


def test_shape_path_escape_into_stories_raises(tmp_path):
    with pytest.raises(PermissionError):
        shape_paths.shape_path(tmp_path, "../../docs/product/stories/x.md")


def test_shape_path_absolute_override_escape_raises(tmp_path):
    outside = tmp_path / "docs" / "product" / "stories" / "sneaky.md"
    with pytest.raises(PermissionError):
        shape_paths.shape_path(tmp_path, str(outside))


def test_shape_path_stays_within_shape_dir(tmp_path):
    resolved = shape_paths.shape_path(tmp_path, "tasks/TASK-1.md")
    expected = (tmp_path / "docs" / "product" / "shape" / "tasks" / "TASK-1.md").resolve()
    assert resolved == expected


# ---------------------------------------------------------------------------
# Author TASK
# ---------------------------------------------------------------------------

def test_author_writes_task_with_serves(tmp_path):
    proj = _proj(tmp_path)
    record = task_model.author(proj, serves=[_STORY_1], title="Build sign-in form")
    assert record["id"] == "TASK-1"
    assert record["serves"] == [_STORY_1]
    assert record["status"] == "open"
    target = proj / "docs" / "product" / "shape" / "tasks" / "TASK-1.md"
    assert target.is_file()
    assert record["path"] == str(target)


def test_task_schema_valid(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    import json

    proj = _proj(tmp_path)
    record = task_model.author(proj, serves=[_STORY_1], title="x", estimate="1-2d")
    schema_path = _SHAPE_SCRIPTS.parent / "schemas" / "task.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=record, schema=schema)


def test_author_rejects_empty_serves(tmp_path):
    proj = _proj(tmp_path)
    with pytest.raises(task_model.TaskError):
        task_model.author(proj, serves=[])


def test_author_rejects_non_string_serves_entry(tmp_path):
    proj = _proj(tmp_path)
    with pytest.raises(task_model.TaskError):
        task_model.author(proj, serves=[_STORY_1, 42])


# ---------------------------------------------------------------------------
# TASK id monotonic alloc
# ---------------------------------------------------------------------------

def test_task_monotonic_alloc(tmp_path):
    proj = _proj(tmp_path)
    first = task_model.author(proj, serves=[_STORY_1])
    second = task_model.author(proj, serves=[_STORY_1])
    assert first["id"] == "TASK-1"
    assert second["id"] == "TASK-2"


def test_task_alloc_continues_from_existing_files(tmp_path):
    proj = _proj(tmp_path)
    task_model.author(proj, serves=[_STORY_1])
    task_model.author(proj, serves=[_STORY_1])
    third = task_model.author(proj, serves=[_STORY_1])
    assert third["id"] == "TASK-3"


def test_task_ids_are_unique_across_authors(tmp_path):
    proj = _proj(tmp_path)
    ids = {task_model.author(proj, serves=[_STORY_1])["id"] for _ in range(4)}
    assert len(ids) == 4


# ---------------------------------------------------------------------------
# Append-only: writing a new task never touches an earlier task's bytes
# ---------------------------------------------------------------------------

def test_append_only_previous_task_byte_unchanged(tmp_path):
    proj = _proj(tmp_path)
    first = task_model.author(proj, serves=[_STORY_1], title="first")
    first_path = Path(first["path"])
    before = first_path.read_bytes()

    task_model.author(proj, serves=[_STORY_1], title="second")

    assert first_path.read_bytes() == before


# ---------------------------------------------------------------------------
# Regression: the PO story file is never touched by a shape write
# ---------------------------------------------------------------------------

def test_stories_byte_unchanged_after_task_write(tmp_path):
    proj = _proj(tmp_path)
    story_path = proj / "docs" / "product" / "stories" / (_STORY_1 + ".md")
    before = story_path.read_bytes()

    task_model.author(proj, serves=[_STORY_1], title="Build sign-in form")

    assert story_path.read_bytes() == before


# ---------------------------------------------------------------------------
# Cardinality: 1-1 / 1-n / n-1, all via the same serves field
# ---------------------------------------------------------------------------

def test_serves_1_to_1(tmp_path):
    proj = _proj(tmp_path)
    t1 = task_model.author(proj, serves=[_STORY_1])
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["story_to_tasks"][_STORY_1] == ["TASK-1"]
    assert result["task_to_stories"]["TASK-1"] == [_STORY_1]
    assert result["dangling"] == {}


def test_serves_1_to_n(tmp_path):
    proj = _proj(tmp_path)
    t1 = task_model.author(proj, serves=[_STORY_1], title="FE form")
    t2 = task_model.author(proj, serves=[_STORY_1], title="BE charge")
    t3 = task_model.author(proj, serves=[_STORY_1], title="integration test")
    result = serves_resolver.resolve_serves(proj, [t1, t2, t3])
    assert result["story_to_tasks"][_STORY_1] == ["TASK-1", "TASK-2", "TASK-3"]
    assert result["task_to_stories"]["TASK-1"] == [_STORY_1]
    assert result["task_to_stories"]["TASK-2"] == [_STORY_1]
    assert result["task_to_stories"]["TASK-3"] == [_STORY_1]


def test_serves_n_to_1(tmp_path):
    proj = _proj(tmp_path)
    story_2 = _add_second_story(proj)
    t1 = task_model.author(proj, serves=[_STORY_1, story_2], title="shared migration")
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["task_to_stories"]["TASK-1"] == [_STORY_1, story_2]
    assert result["story_to_tasks"][_STORY_1] == ["TASK-1"]
    assert result["story_to_tasks"][story_2] == ["TASK-1"]
    assert result["dangling"] == {}


def test_serves_dangling_is_flagged_not_dropped(tmp_path):
    proj = _proj(tmp_path)
    t1 = task_model.author(proj, serves=[_STORY_1, "PRD-GHOST-E1-S9"])
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["dangling"]["TASK-1"] == ["PRD-GHOST-E1-S9"]
    # the resolvable id must still resolve normally, not be crowded out
    assert result["story_to_tasks"][_STORY_1] == ["TASK-1"]


def test_resolve_serves_duplicate_task_id_keeps_maps_consistent(tmp_path):
    # Two task records colliding on the same id (copy-paste, forgot to rename)
    # must NOT make the two coverage maps contradict. task_to_stories used to
    # last-write-wins and silently drop the earlier record's edge while
    # story_to_tasks kept both -> the reverse map lost a real link.
    proj = _proj(tmp_path)
    story_2 = _add_second_story(proj)
    dup_a = {"id": "TASK-1", "serves": [_STORY_1]}
    dup_b = {"id": "TASK-1", "serves": [story_2]}
    result = serves_resolver.resolve_serves(proj, [dup_a, dup_b])
    # task_to_stories must keep BOTH edges (union), agreeing with story_to_tasks.
    assert set(result["task_to_stories"]["TASK-1"]) == {_STORY_1, story_2}
    assert result["story_to_tasks"][_STORY_1] == ["TASK-1"]
    assert result["story_to_tasks"][story_2] == ["TASK-1"]


def test_resolve_serves_duplicate_task_id_same_story_no_double_count(tmp_path):
    # Two records with the same id serving the SAME story must not inflate
    # story_to_tasks to [TASK-1, TASK-1] -- one edge, one entry.
    proj = _proj(tmp_path)
    dup_a = {"id": "TASK-1", "serves": [_STORY_1]}
    dup_b = {"id": "TASK-1", "serves": [_STORY_1]}
    result = serves_resolver.resolve_serves(proj, [dup_a, dup_b])
    assert result["story_to_tasks"][_STORY_1] == ["TASK-1"]
    assert result["task_to_stories"]["TASK-1"] == [_STORY_1]


def test_resolve_serves_duplicate_task_id_dangling_edge_not_double_counted(tmp_path):
    # The `dangling` map is the third accumulator in resolve_serves and must be
    # deduped on a duplicate task id the same way task_to_stories/story_to_tasks
    # are: two records colliding on TASK-1 that each serve the SAME unresolved
    # (ghost) story must report ONE dangling edge, not two. A plain append/extend
    # inflated the diagnostic count while task_to_stories (deduped) stayed at one.
    proj = _proj(tmp_path)
    ghost = "PRD-GHOST-E1-S9"
    dup_a = {"id": "TASK-1", "serves": [ghost]}
    dup_b = {"id": "TASK-1", "serves": [ghost]}
    result = serves_resolver.resolve_serves(proj, [dup_a, dup_b])
    assert result["dangling"]["TASK-1"] == [ghost]


def test_resolve_serves_duplicate_task_id_invalid_serves_not_double_counted(tmp_path):
    # Same asymmetry on the invalid-entry path: two dup-id records each carrying
    # a malformed `serves: [5]` are one malformed edge, not two -- dangling must
    # dedupe the str-coerced invalid id just like the valid dangling story above.
    proj = _proj(tmp_path)
    dup_a = {"id": "TASK-1", "serves": [5]}
    dup_b = {"id": "TASK-1", "serves": [5]}
    result = serves_resolver.resolve_serves(proj, [dup_a, dup_b])
    assert result["dangling"]["TASK-1"] == ["5"]


def test_resolve_serves_from_dir_reads_committed_tasks(tmp_path):
    proj = _proj(tmp_path)
    task_model.author(proj, serves=[_STORY_1])
    result = serves_resolver.resolve_serves_from_dir(proj)
    assert result["story_to_tasks"][_STORY_1] == ["TASK-1"]


# ---------------------------------------------------------------------------
# list_tasks / read_task round-trip
# ---------------------------------------------------------------------------

def test_list_tasks_sorted_by_numeric_id(tmp_path):
    proj = _proj(tmp_path)
    task_model.author(proj, serves=[_STORY_1])
    task_model.author(proj, serves=[_STORY_1])
    listed = task_model.list_tasks(proj)
    assert [t["id"] for t in listed] == ["TASK-1", "TASK-2"]


def test_list_tasks_includes_non_canonical_zero_padded_filename(tmp_path):
    """A hand-authored TASK-02.md (zero-padded, still matches
    _TASK_FILE_RE) must not vanish from list_tasks() -- it must stay
    consistent with serves_resolver.list_task_records(), which reads the
    actual glob-matched file path rather than re-deriving a canonical
    `TASK-%d` path that may not exist on disk."""
    proj = _proj(tmp_path)
    task_model.author(proj, serves=[_STORY_1], title="canonical")
    (_tasks_dir(proj) / "TASK-02.md").write_text(
        "---\nid: TASK-2\nserves: [%s]\ntitle: hand-authored\ndepends_on: []\n"
        "acceptance: []\nstatus: open\n---\n\n# TASK-02\n" % _STORY_1,
        encoding="utf-8",
    )
    listed_ids = [t["id"] for t in task_model.list_tasks(proj)]
    resolver_ids = [r["id"] for r in serves_resolver.list_task_records(proj)]
    assert listed_ids == ["TASK-1", "TASK-2"]
    assert listed_ids == resolver_ids


def test_list_tasks_skips_non_string_id_matches_list_task_records(tmp_path):
    """A hand-edited `id: [TASK-2, TASK-3]` (YAML-parses to a LIST, not a
    scalar) or a missing `id:` key can never name a real task -- list_tasks()
    must skip it, the SAME way serves_resolver.list_task_records() does, so the
    two readers cannot disagree on membership and loop_handoff never embeds a
    raw non-scalar id into a human-facing brief."""
    proj = _proj(tmp_path)
    task_model.author(proj, serves=[_STORY_1], title="good")
    (_tasks_dir(proj) / "TASK-2.md").write_text(
        "---\nid: [TASK-2, TASK-3]\nserves: [%s]\n---\n\nbad\n" % _STORY_1,
        encoding="utf-8",
    )
    (_tasks_dir(proj) / "TASK-3.md").write_text(
        "---\nserves: [%s]\ntitle: no-id\n---\n\nno id key\n" % _STORY_1,
        encoding="utf-8",
    )
    listed_ids = [t["id"] for t in task_model.list_tasks(proj)]
    resolver_ids = [r["id"] for r in serves_resolver.list_task_records(proj)]
    assert listed_ids == ["TASK-1"]
    assert listed_ids == resolver_ids


def test_read_task_unknown_id_raises_clear_error(tmp_path):
    proj = _proj(tmp_path)
    with pytest.raises(task_model.TaskError):
        task_model.read_task(proj, "TASK-999")


# ---------------------------------------------------------------------------
# Fail-open on a malformed frontmatter YAML: PyYAML's timestamp constructor
# raises a bare ValueError (not yaml.YAMLError) on an out-of-range unquoted
# date; a non-UTF-8 file raises UnicodeDecodeError. Both must surface as a
# clear TaskError, never a raw parser traceback.
# ---------------------------------------------------------------------------

def _tasks_dir(proj: Path) -> Path:
    d = proj / "docs" / "product" / "shape" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_read_task_bad_date_frontmatter_fails_open_not_crash(tmp_path):
    proj = _proj(tmp_path)
    (_tasks_dir(proj) / "TASK-1.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "serves: [%s]\n"
        "title: t\n"
        "estimate: ''\n"
        "depends_on: []\n"
        "acceptance: []\n"
        "status: open\n"
        "ts: 2026-13-99\n"
        "---\n\n# TASK-1\n" % _STORY_1,
        encoding="utf-8",
    )
    with pytest.raises(task_model.TaskError):
        task_model.read_task(proj, "TASK-1")


def test_read_task_non_utf8_file_fails_open_not_crash(tmp_path):
    proj = _proj(tmp_path)
    (_tasks_dir(proj) / "TASK-1.md").write_bytes(
        b"---\nid: TASK-1\ntitle: \xff\xfe bad\n---\n\nbody\n"
    )
    with pytest.raises(task_model.TaskError):
        task_model.read_task(proj, "TASK-1")


# ---------------------------------------------------------------------------
# list_tasks / list_task_records must never surface a raw traceback over one
# malformed hand-edited record -- skip it and keep listing the rest.
# ---------------------------------------------------------------------------

def test_list_tasks_skips_malformed_record_not_crash(tmp_path):
    proj = _proj(tmp_path)
    good = task_model.author(proj, serves=[_STORY_1], title="good")
    (_tasks_dir(proj) / "TASK-2.md").write_text(
        "---\nid: TASK-2\nts: 2026-13-99\n---\n\nbad\n", encoding="utf-8",
    )
    listed = task_model.list_tasks(proj)
    assert [t["id"] for t in listed] == [good["id"]]


def test_list_task_records_skips_non_utf8_file_not_crash(tmp_path):
    proj = _proj(tmp_path)
    task_model.author(proj, serves=[_STORY_1], title="good")
    (_tasks_dir(proj) / "TASK-2.md").write_bytes(
        b"---\nid: TASK-2\ntitle: \xff\xfe bad\n---\n\nbody\n"
    )
    records = serves_resolver.list_task_records(proj)
    assert [r["id"] for r in records] == ["TASK-1"]


# ---------------------------------------------------------------------------
# list_task_records must sort NUMERICALLY by the TASK-<n> number, matching
# task_model.list_tasks() -- a bare `sorted(d.glob(...))` string-sorts
# "TASK-10.md" before "TASK-2.md".
# ---------------------------------------------------------------------------

def test_list_task_records_sorted_numerically_not_lexicographically(tmp_path):
    proj = _proj(tmp_path)
    for _ in range(9):
        task_model.author(proj, serves=[_STORY_1])  # TASK-1 .. TASK-9
    task_model.author(proj, serves=[_STORY_1])  # TASK-10
    records = serves_resolver.list_task_records(proj)
    assert [r["id"] for r in records] == ["TASK-%d" % n for n in range(1, 11)]


def test_story_to_tasks_lists_task_2_before_task_10(tmp_path):
    proj = _proj(tmp_path)
    (_tasks_dir(proj) / "TASK-10.md").write_text(
        "---\nid: TASK-10\nserves: [%s]\ntitle: t\ndepends_on: []\n"
        "acceptance: []\nstatus: open\n---\n\n# TASK-10\n" % _STORY_1,
        encoding="utf-8",
    )
    (_tasks_dir(proj) / "TASK-2.md").write_text(
        "---\nid: TASK-2\nserves: [%s]\ntitle: t\ndepends_on: []\n"
        "acceptance: []\nstatus: open\n---\n\n# TASK-2\n" % _STORY_1,
        encoding="utf-8",
    )
    result = serves_resolver.resolve_serves_from_dir(proj)
    assert result["story_to_tasks"][_STORY_1] == ["TASK-2", "TASK-10"]


# ---------------------------------------------------------------------------
# _TASK_FILE_RE must be defined ONCE (in task_model.py) and imported by
# serves_resolver.py -- not redefined -- so the two readers cannot silently
# drift on the TASK-<n>.md naming scheme.
# ---------------------------------------------------------------------------

def test_task_file_regex_is_a_single_shared_definition():
    assert serves_resolver._TASK_FILE_RE is task_model._TASK_FILE_RE


# ---------------------------------------------------------------------------
# Drift guard: task_model.STATUSES must not silently diverge from
# task.schema.json's status enum.
# ---------------------------------------------------------------------------

def test_task_statuses_match_schema_enum():
    import json

    schema_path = _SHAPE_SCRIPTS.parent / "schemas" / "task.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert set(task_model.STATUSES) == set(schema["properties"]["status"]["enum"])


# ---------------------------------------------------------------------------
# serves_resolver: a duplicate story id within one task's own `serves` must
# not double-count into story_to_tasks.
# ---------------------------------------------------------------------------

def test_serves_dup_within_task_deduped_not_double_counted(tmp_path):
    proj = _proj(tmp_path)
    t1 = {"id": "TASK-1", "serves": [_STORY_1, _STORY_1]}
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["story_to_tasks"][_STORY_1] == ["TASK-1"]
    assert result["task_to_stories"]["TASK-1"] == [_STORY_1]


# ---------------------------------------------------------------------------
# A non-hashable `serves` entry (a hand-edited `serves:[[x]]` or
# `serves:[{}]`) must not TypeError the set-based dedup/story-lookup --
# diverges from strict_gate.py's isinstance(str) guard otherwise. A
# non-string entry can never resolve to a live story id anyway, so it is
# flagged dangling the same way a dangling string id already is.
# ---------------------------------------------------------------------------

def test_serves_list_entry_flagged_dangling_not_crash(tmp_path):
    proj = _proj(tmp_path)
    t1 = {"id": "TASK-1", "serves": [_STORY_1, [_STORY_1]]}
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["story_to_tasks"][_STORY_1] == ["TASK-1"]
    # Routed through id_grammar.normalize_serves now, which str-coerces
    # every invalid entry (so the CLI's json.dumps can never choke on a
    # non-serializable value) -- the raw list is no longer preserved as-is
    # in `dangling`.
    assert result["dangling"]["TASK-1"] == [str([_STORY_1])]


def test_serves_dict_entry_flagged_dangling_not_crash(tmp_path):
    proj = _proj(tmp_path)
    t1 = {"id": "TASK-1", "serves": [{}]}
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["dangling"]["TASK-1"] == [str({})]
    assert result["story_to_tasks"] == {}


# ---------------------------------------------------------------------------
# `serves` is read through the shared `id_grammar.normalize_serves` so
# serves_resolver cannot disagree with strict_gate about what a malformed
# `serves` means -- a non-list value is flagged dangling (not silently
# emptied), and a non-string list entry (a YAML-auto-resolved `datetime.date`
# from `serves: [2026-07-13]`, a bare int) is str-coerced so main()'s
# json.dumps never crashes.
# ---------------------------------------------------------------------------

def test_resolve_serves_dedupes_duplicate_invalid_entry(tmp_path):
    # strict_gate.check_shape_serves dedupes BOTH valid and invalid serves;
    # resolve_serves must match -- their docstrings claim the two shared
    # readers cannot silently disagree about a malformed `serves`.
    proj = _proj(tmp_path)
    result = serves_resolver.resolve_serves(proj, [{"id": "TASK-1", "serves": [1, 1]}])
    assert result["task_to_stories"]["TASK-1"] == ["1"]
    assert result["dangling"]["TASK-1"] == ["1"]


def test_resolve_serves_non_list_bare_string_flagged_dangling(tmp_path):
    proj = _proj(tmp_path)
    t1 = {"id": "TASK-1", "serves": "STORY-1"}
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["dangling"]["TASK-1"] == ["STORY-1"]
    assert result["story_to_tasks"] == {}


def test_resolve_serves_int_value_flagged_dangling(tmp_path):
    proj = _proj(tmp_path)
    t1 = {"id": "TASK-1", "serves": 5}
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["dangling"]["TASK-1"] == ["5"]


def test_resolve_serves_date_entry_flagged_dangling_and_json_safe(tmp_path):
    import datetime
    import json

    proj = _proj(tmp_path)
    t1 = {"id": "TASK-1", "serves": [datetime.date(2026, 7, 13)]}
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["dangling"]["TASK-1"] == ["2026-07-13"]
    json.dumps(result)  # must not TypeError on a raw datetime.date


def test_resolve_serves_from_dir_cli_exits_cleanly_on_malformed_serves(tmp_path):
    proj = _proj(tmp_path)
    tasks_dir = _tasks_dir(proj)
    (tasks_dir / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: STORY-1\n---\n\n# TASK-1\n", encoding="utf-8",
    )
    (tasks_dir / "TASK-2.md").write_text(
        "---\nid: TASK-2\nserves: [2026-07-13]\n---\n\n# TASK-2\n", encoding="utf-8",
    )
    (tasks_dir / "TASK-3.md").write_text(
        "---\nid: TASK-3\nserves: 5\n---\n\n# TASK-3\n", encoding="utf-8",
    )
    (tasks_dir / "TASK-4.md").write_text(
        "---\nid: TASK-4\nserves: [[x]]\n---\n\n# TASK-4\n", encoding="utf-8",
    )
    rc = serves_resolver.main(["--root", str(proj)])
    assert rc == 0


# ---------------------------------------------------------------------------
# A `!!timestamp` explicit-tag frontmatter value raises a bare
# AttributeError from PyYAML's construct_yaml_timestamp -- NOT yaml.YAMLError
# or ValueError -- and must still fail open, never a raw parser traceback.
# ---------------------------------------------------------------------------

def test_read_task_yaml_timestamp_tag_frontmatter_fails_open_not_crash(tmp_path):
    proj = _proj(tmp_path)
    (_tasks_dir(proj) / "TASK-1.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "serves: [%s]\n"
        "title: t\n"
        "estimate: ''\n"
        "depends_on: []\n"
        "acceptance: []\n"
        "status: open\n"
        "ts: !!timestamp 'not a ts'\n"
        "---\n\n# TASK-1\n" % _STORY_1,
        encoding="utf-8",
    )
    with pytest.raises(task_model.TaskError):
        task_model.read_task(proj, "TASK-1")


def test_list_task_records_skips_yaml_timestamp_tag_record_not_crash(tmp_path):
    proj = _proj(tmp_path)
    task_model.author(proj, serves=[_STORY_1], title="good")
    (_tasks_dir(proj) / "TASK-2.md").write_text(
        "---\nid: TASK-2\nts: !!timestamp 'not a ts'\n---\n\nbad\n", encoding="utf-8",
    )
    records = serves_resolver.list_task_records(proj)
    assert [r["id"] for r in records] == ["TASK-1"]


# ---------------------------------------------------------------------------
# `task_model.py --list` prints a tab-separated `id, status, serves` line
# per task. A hand-edited `serves` can be any YAML shape (a bare string, a
# YAML-auto-resolved date, a bare int) -- the old bare `",".join(serves)`
# char-iterated a bare string and TypeError'd on a non-iterable, bricking the
# whole `--list` output over one bad record. Routed through the same
# `id_grammar.normalize_serves` every other `serves` reader in this codebase
# already shares.
# ---------------------------------------------------------------------------

def test_list_cli_bare_string_serves_does_not_crash(tmp_path, capsys):
    proj = _proj(tmp_path)
    (_tasks_dir(proj) / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: STORY-1\ntitle: t\nstatus: open\n---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    rc = task_model.main(["--root", str(proj), "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TASK-1" in out
    assert "STORY-1" in out


def test_list_cli_date_entry_serves_does_not_crash(tmp_path, capsys):
    proj = _proj(tmp_path)
    (_tasks_dir(proj) / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: [2026-07-13]\ntitle: t\nstatus: open\n---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    rc = task_model.main(["--root", str(proj), "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TASK-1" in out
    assert "2026-07-13" in out


def test_list_cli_int_serves_does_not_crash(tmp_path, capsys):
    proj = _proj(tmp_path)
    (_tasks_dir(proj) / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: 5\ntitle: t\nstatus: open\n---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    rc = task_model.main(["--root", str(proj), "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TASK-1" in out


# ---------------------------------------------------------------------------
# A non-string `id` (a hand-edited task file where `id: [TASK-2, TASK-3]`
# YAML-parses to a LIST, not a scalar) is unhashable -- resolve_serves used to
# key `task_to_stories[task_id] = ...` directly off it and TypeError the
# whole resolve/CLI pass over one bad record, defeating "one bad task can't
# block the rest" (the same contract list_task_records already upholds for a
# malformed-YAML/non-UTF-8 file). A non-str id can never name a real task
# anyway, so it is skipped, not str-coerced into a fake key.
# ---------------------------------------------------------------------------

def test_resolve_serves_list_id_skipped_not_unhashable_crash(tmp_path):
    proj = _proj(tmp_path)
    t1 = {"id": [_STORY_1], "serves": [_STORY_1]}
    t2 = {"id": "TASK-2", "serves": [_STORY_1]}
    result = serves_resolver.resolve_serves(proj, [t1, t2])
    assert result["task_to_stories"] == {"TASK-2": [_STORY_1]}
    assert result["story_to_tasks"][_STORY_1] == ["TASK-2"]


def test_resolve_serves_dict_id_skipped_not_unhashable_crash(tmp_path):
    proj = _proj(tmp_path)
    t1 = {"id": {"a": 1}, "serves": [_STORY_1]}
    result = serves_resolver.resolve_serves(proj, [t1])
    assert result["task_to_stories"] == {}
    assert result["dangling"] == {}


def test_list_task_records_skips_non_string_id_not_crash(tmp_path):
    proj = _proj(tmp_path)
    task_model.author(proj, serves=[_STORY_1], title="good")
    (_tasks_dir(proj) / "TASK-2.md").write_text(
        "---\nid: [TASK-2, TASK-3]\nserves: [%s]\n---\n\nbad\n" % _STORY_1,
        encoding="utf-8",
    )
    records = serves_resolver.list_task_records(proj)
    assert [r["id"] for r in records] == ["TASK-1"]


def test_resolve_serves_from_dir_cli_exits_cleanly_on_list_id_task(tmp_path):
    proj = _proj(tmp_path)
    tasks_dir = _tasks_dir(proj)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_model.author(proj, serves=[_STORY_1])
    (tasks_dir / "TASK-2.md").write_text(
        "---\nid: [TASK-2, TASK-3]\nserves: [%s]\n---\n\n# TASK-2\n" % _STORY_1,
        encoding="utf-8",
    )
    rc = serves_resolver.main(["--root", str(proj)])
    assert rc == 0


def test_sidecar_helpers_shared_across_shape_modules():
    """`_default_actor`/`_now_iso`/`_render_file` must have exactly ONE
    definition site (`_sidecar.py`) -- checked by function IDENTITY (`is`),
    not just matching behavior, across every hs:shape sidecar writer that
    uses them (task_model/experiment_spec/poc_gate/roadmap_rollup). A
    reintroduced local copy in any one of them would still pass a
    behavior-only check but fails this one."""
    mods = load_skill_scripts(
        _SHAPE_SCRIPTS,
        ["_sidecar", "shape_paths", "task_model", "poc_gate", "roadmap_rollup",
         "experiment_spec", "effort_map"],
    )
    sidecar = mods["_sidecar"]
    for name in ("task_model", "poc_gate", "roadmap_rollup", "experiment_spec"):
        mod = mods[name]
        assert mod._default_actor is sidecar._default_actor, name
        assert mod._now_iso is sidecar._now_iso, name
        # Each writer now goes through the shared atomic write chokepoint
        # (`write_record`) instead of calling `_render_file` + a bare write_text
        # itself — identity-checked so a reintroduced local copy is caught.
        assert mod.write_record is sidecar.write_record, name


def test_shape_readers_share_bridge_loaders_no_local_copies():
    # DRY: the isolated-spec-module loader wrappers (_load_frontmatter_parser /
    # _load_spec_graph) must live ONCE in _spec_bridge, not be re-defined
    # byte-for-byte in every reader. Each reader imports them from the bridge.
    scripts = ROOT / "harness" / "plugins" / "hs" / "skills" / "shape" / "scripts"
    for name in ("experiment_spec", "poc_gate", "roadmap_rollup", "serves_resolver", "task_model"):
        src = (scripts / (name + ".py")).read_text(encoding="utf-8")
        assert "def _load_frontmatter_parser" not in src, \
            f"{name} re-defines _load_frontmatter_parser (import from _spec_bridge)"
        assert "def _load_spec_graph" not in src, \
            f"{name} re-defines _load_spec_graph (import from _spec_bridge)"
    bridge = (scripts / "_spec_bridge.py").read_text(encoding="utf-8")
    assert "def load_frontmatter_parser" in bridge
    assert "def load_spec_graph" in bridge


def test_list_task_records_skips_non_regular_file_without_hanging(tmp_path):
    # A FIFO named TASK-*.md in the tasks sidecar would block read_text forever.
    # list_task_records must skip a non-regular glob match (its `except OSError`
    # never fires -- the read BLOCKS before raising). SIGALRM bounds the test so
    # a regression FAILS instead of hanging the suite.
    import os
    import signal
    proj = _proj(tmp_path)
    tasks = _tasks_dir(proj)
    os.mkfifo(tasks / "TASK-1.md")
    (tasks / "TASK-2.md").write_text(
        "---\nid: TASK-2\nserves: []\n---\n\n# Task 2\n", encoding="utf-8")

    class _Blocked(Exception):
        pass

    def _handler(signum, frame):
        raise _Blocked

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, 4)
    try:
        records = serves_resolver.list_task_records(proj)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    # the FIFO is skipped; the real TASK-2 still resolves
    assert [r.get("id") for r in records] == ["TASK-2"]
