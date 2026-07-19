"""hs:shape — roadmap + effort rollup (BA sidecar).

`effort_map.py` maps a PO story `size:S|M|L` to a BA day range (or sums a batch
of already-explicit BA estimate strings); `roadmap_rollup.py` groups dev tasks
into milestones, rolls their effort up, and gates each milestone on the
technical-POC precondition read from the POC sidecar (`poc_gate.py`) -- see
test_gated_task_excluded_when_poc_not_closed / test_no_poc_dir_fails_open_advisory
below, hard behavioral guards on that one-direction data flow (technical-POC ->
roadmap, never the reverse), not just documentation.
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

_mods = load_skill_scripts(
    _SHAPE_SCRIPTS, ["shape_paths", "task_model", "poc_gate", "roadmap_rollup", "effort_map"]
)
shape_paths = _mods["shape_paths"]
task_model = _mods["task_model"]
poc_gate = _mods["poc_gate"]
roadmap_rollup = _mods["roadmap_rollup"]
effort_map = _mods["effort_map"]

_STORY_1 = "PRD-AUTH-E1-S1"  # from conftest.VALID's fixture tree


def _proj(tmp_path: Path) -> Path:
    return make_proj(tmp_path, git=False)


def _task(proj: Path, estimate: str = "", title: str = "t"):
    return task_model.author(proj, serves=[_STORY_1], title=title, estimate=estimate)


# ---------------------------------------------------------------------------
# effort_map: size -> range
# ---------------------------------------------------------------------------

def test_map_size_to_range_default_table():
    assert effort_map.map_size_to_range("M") == "3-5d"
    assert effort_map.map_size_to_range("S") == "1-2d"
    assert effort_map.map_size_to_range("L") == "1-2w"


def test_map_size_to_range_unknown_size_returns_none():
    assert effort_map.map_size_to_range("XL") is None
    assert effort_map.map_size_to_range(None) is None


def test_estimate_for_task_falls_back_to_story_size():
    task = {"id": "TASK-1", "estimate": ""}
    assert effort_map.estimate_for_task(task, story_size="M") == "3-5d"


# ---------------------------------------------------------------------------
# effort_map: explicit BA estimate overrides the size-derived range
# ---------------------------------------------------------------------------

def test_estimate_for_task_explicit_estimate_wins_over_size():
    task = {"id": "TASK-1", "estimate": "2d"}
    assert effort_map.estimate_for_task(task, story_size="M") == "2d"


def test_estimate_for_task_no_estimate_no_size_is_blank():
    assert effort_map.estimate_for_task({"id": "TASK-1"}) == ""


# ---------------------------------------------------------------------------
# A truthy but UNPARSABLE explicit estimate ("-2d" -- a negative, not matched
# by `_ESTIMATE_RE`) used to be returned as-is (an explicit BA figure "always
# wins"), riding silently into the caller's `estimates` and getting dropped
# to a silent 0 once `sum_estimates` failed to parse it -- even though a
# linked story's size could have supplied a real range. It must now fall
# back to the size-derived range, the same as a blank/non-string estimate.
# ---------------------------------------------------------------------------

def test_estimate_for_task_unparsable_explicit_falls_back_to_story_size():
    task = {"id": "TASK-1", "estimate": "-2d"}
    assert effort_map.estimate_for_task(task, story_size="M") == "3-5d"


# ---------------------------------------------------------------------------
# effort_map: sum estimates
# ---------------------------------------------------------------------------

def test_sum_estimates_plain_days():
    assert effort_map.sum_estimates(["2d", "3d", "5d"]) == "10d"


def test_sum_estimates_ranges_sum_each_side():
    assert effort_map.sum_estimates(["1-2d", "3-5d"]) == "4-7d"


def test_sum_estimates_week_converts_to_days():
    assert effort_map.sum_estimates(["1w"]) == "5d"


def test_sum_estimates_skips_unparsable_entries():
    assert effort_map.sum_estimates(["2d", "not-an-estimate", "3d"]) == "5d"


def test_sum_estimates_empty_batch_is_zero_days():
    assert effort_map.sum_estimates([]) == "0d"
    assert effort_map.sum_estimates(["garbage"]) == "0d"


def test_parse_estimate_rejects_inverted_range():
    # A `"5-2d"` fat-finger (lo > hi) must be treated as unparsable (-> None),
    # not silently kept as (5, 2) which would persist a nonsensical min > max
    # effort_rollup ("6-4d") with no warning.
    assert effort_map.parse_estimate_days("5-2d") is None
    assert effort_map.parse_estimate_days("2-5d") == (2.0, 5.0)   # valid unchanged
    # the inverted entry drops out of a batch sum like any other unparsable one
    assert effort_map.sum_estimates(["5-2d", "1-2d"]) == "1-2d"


# ---------------------------------------------------------------------------
# load_size_range_table's except must widen to (yaml.YAMLError,
# ValueError) -- PyYAML's timestamp constructor raises a bare ValueError
# (not yaml.YAMLError) for an out-of-range unquoted date, the same hazard
# every sibling sidecar reader in this codebase already guards against.
# ---------------------------------------------------------------------------

def test_load_size_range_table_bad_date_value_fails_open_not_crash(tmp_path):
    table_path = tmp_path / "table.yaml"
    table_path.write_text("S: \"1-2d\"\nbad: 2026-13-99\n", encoding="utf-8")
    table = effort_map.load_size_range_table(table_path)
    assert table == effort_map.default_size_range_table()


# ---------------------------------------------------------------------------
# A `!!timestamp` explicit-tag value raises a bare AttributeError from
# PyYAML's construct_yaml_timestamp -- not yaml.YAMLError or ValueError --
# and must still fail open, never a raw parser traceback.
# ---------------------------------------------------------------------------

def test_load_size_range_table_yaml_timestamp_tag_fails_open_not_crash(tmp_path):
    table_path = tmp_path / "table.yaml"
    table_path.write_text("S: \"1-2d\"\nbad: !!timestamp 'not a ts'\n", encoding="utf-8")
    table = effort_map.load_size_range_table(table_path)
    assert table == effort_map.default_size_range_table()


# ---------------------------------------------------------------------------
# roadmap_rollup: rollup per milestone (sum of task estimates)
# ---------------------------------------------------------------------------

def test_rollup_sums_task_estimates(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    t2 = _task(proj, estimate="3d")
    t3 = _task(proj, estimate="5d")

    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", title="Sign-in", task_ids=[t1["id"], t2["id"], t3["id"]],
    )

    assert milestone["effort_rollup"] == "10d"
    assert milestone["contains"] == [t1["id"], t2["id"], t3["id"]]
    assert milestone["excluded"] == []


def test_rollup_empty_task_list_is_zero_days_not_a_crash(tmp_path):
    proj = _proj(tmp_path)
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[])
    assert milestone["effort_rollup"] == "0d"
    assert milestone["contains"] == []


def test_rollup_unknown_task_id_is_excluded_not_a_crash(tmp_path):
    proj = _proj(tmp_path)
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=["TASK-999"])
    assert milestone["contains"] == []
    assert milestone["excluded"][0]["task_id"] == "TASK-999"
    assert milestone["excluded"][0]["reason"] == "task_not_found"


def test_build_milestone_rejects_bad_id(tmp_path):
    proj = _proj(tmp_path)
    with pytest.raises(roadmap_rollup.RoadmapError):
        roadmap_rollup.build_milestone(proj, "not-a-milestone-id")


# ---------------------------------------------------------------------------
# `task_ids` can carry the same TASK-<n> twice (a hand-edited or re-run
# `--task-ids TASK-1,TASK-1`) -- unlike `serves_resolver`'s own dedup on the
# story side, `build_milestone` never deduped its own `task_ids`, so a
# repeated id double-counted that task's effort into `effort_rollup`.
# ---------------------------------------------------------------------------

def test_build_milestone_dedupes_repeated_task_ids(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")

    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=[t1["id"], t1["id"]],
    )

    assert milestone["contains"] == [t1["id"]]
    assert milestone["effort_rollup"] == "2d"


# ---------------------------------------------------------------------------
# roadmap_rollup: technical-POC precondition gates a milestone
# ---------------------------------------------------------------------------

def test_gated_task_included_when_poc_closed(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    poc = poc_gate.author(proj, subject="feasibility check")
    review = proj / "artifacts" / "review-decision.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text('{"verdict": "PASS"}', encoding="utf-8")
    verification = proj / "artifacts" / "verification.json"
    verification.write_text('{"verdict": "PASS"}', encoding="utf-8")
    poc_gate.gate(proj, poc["id"], review_decision_path=review, verification_path=verification)

    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=[t1["id"]], task_poc_map={t1["id"]: poc["id"]},
    )

    assert milestone["contains"] == [t1["id"]]
    assert milestone["excluded"] == []
    assert milestone["poc_gated"] is True


def test_gated_task_excluded_when_poc_not_closed(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    poc = poc_gate.author(proj, subject="feasibility check still open")

    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=[t1["id"]], task_poc_map={t1["id"]: poc["id"]},
    )

    assert milestone["contains"] == []
    assert milestone["excluded"][0]["task_id"] == t1["id"]
    assert milestone["excluded"][0]["reason"] == "poc_not_closed"
    assert milestone["poc_gated"] is False


def test_ungated_task_included_regardless_of_poc_state(tmp_path):
    """A task with no entry in task_poc_map has no precondition declared at
    all -- it rolls up unconditionally, whether or not any POC exists."""
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")

    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[t1["id"]])

    assert milestone["contains"] == [t1["id"]]
    assert milestone["poc_gated"] is False  # nothing declared -- advisory, not verified


def test_no_poc_dir_fails_open_advisory_no_crash(tmp_path):
    """docs/product/shape/poc/ has never been created at all -- a gate
    referencing an id that cannot possibly exist yet must not raise."""
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    assert not (proj / "docs" / "product" / "shape" / "poc").exists()

    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=[t1["id"]], task_poc_map={t1["id"]: "POC-1"},
    )

    assert milestone["contains"] == []
    assert milestone["excluded"][0]["reason"] == "poc_unknown"
    assert milestone["poc_gated"] is False


def test_poc_closed_status_is_none_when_nothing_declared(tmp_path):
    proj = _proj(tmp_path)
    assert roadmap_rollup.poc_closed_status(proj, None) is None
    assert roadmap_rollup.poc_closed_status(proj, "POC-1") is None  # not authored yet


# ---------------------------------------------------------------------------
# roadmap_rollup: depends_on cycle detection, never hangs
# ---------------------------------------------------------------------------

def test_detect_cycles_flags_two_node_cycle():
    milestones = [
        {"id": "MS-1", "depends_on": ["MS-2"]},
        {"id": "MS-2", "depends_on": ["MS-1"]},
    ]
    assert roadmap_rollup.detect_cycles(milestones) == {"MS-1", "MS-2"}


def test_detect_cycles_no_cycle_in_a_chain():
    milestones = [
        {"id": "MS-1", "depends_on": []},
        {"id": "MS-2", "depends_on": ["MS-1"]},
        {"id": "MS-3", "depends_on": ["MS-2"]},
    ]
    assert roadmap_rollup.detect_cycles(milestones) == set()


def test_detect_cycles_self_loop():
    milestones = [{"id": "MS-1", "depends_on": ["MS-1"]}]
    assert roadmap_rollup.detect_cycles(milestones) == {"MS-1"}


def test_detect_cycles_dangling_depends_on_id_is_skipped_not_crashed():
    milestones = [{"id": "MS-1", "depends_on": ["MS-999"]}]
    assert roadmap_rollup.detect_cycles(milestones) == set()


# ---------------------------------------------------------------------------
# A diamond where BOTH branches close a real cycle through the shared
# descendant: MS-1->[MS-2,MS-3], MS-2->MS-4, MS-3->MS-4, MS-4->MS-1. The old
# 3-color DFS marked MS-4 BLACK (fully explored) after the MS-1->MS-2->MS-4
# branch found its back-edge to MS-1, so the MS-1->MS-3->MS-4 branch never
# re-examined MS-4's own back-edge to MS-1 and MS-3 was dropped from
# membership -- order-dependent under-count. All 4 nodes are mutually
# reachable (one SCC of size 4), so all 4 must be in-cycle.
# ---------------------------------------------------------------------------

def test_detect_cycles_diamond_both_branches_marked_in_cycle():
    milestones = [
        {"id": "MS-1", "depends_on": ["MS-2", "MS-3"]},
        {"id": "MS-2", "depends_on": ["MS-4"]},
        {"id": "MS-3", "depends_on": ["MS-4"]},
        {"id": "MS-4", "depends_on": ["MS-1"]},
    ]
    assert roadmap_rollup.detect_cycles(milestones) == {"MS-1", "MS-2", "MS-3", "MS-4"}


# ---------------------------------------------------------------------------
# A hand-edited `depends_on` can land on disk as a bare (non-list) value.
# The old `list(depends_on)` char-iterated a bare string -- "MS-2" becomes
# ['M','S','-','2'], none of which ever matches a real "MS-<n>" id, so a
# real dependency (and the cycle it would have completed) went undetected
# -- and TypeError'd outright on a non-iterable like a bare int. A bare
# string reads as the one dependency it names; a bare int cannot express
# any id and is dropped rather than crashing `list()`.
# ---------------------------------------------------------------------------

def test_detect_cycles_bare_string_depends_on_recovers_the_dependency():
    milestones = [
        {"id": "MS-1", "depends_on": "MS-2"},  # bare string, not a list
        {"id": "MS-2", "depends_on": ["MS-1"]},
    ]
    assert roadmap_rollup.detect_cycles(milestones) == {"MS-1", "MS-2"}


def test_detect_cycles_int_depends_on_does_not_crash():
    milestones = [{"id": "MS-1", "depends_on": 5}]
    assert roadmap_rollup.detect_cycles(milestones) == set()


# ---------------------------------------------------------------------------
# Two milestone entries can carry the SAME id (a hand-edited roadmap.md, or
# two `assemble_roadmap` specs both naming MS-1 -- add_milestone_locked
# dedupes but assemble does not). The graph was built with a plain dict
# comprehension keyed by id, so the LAST same-id record silently overwrote
# every earlier copy's `depends_on` -- if the cyclic edge lived on an earlier
# copy and a later copy declared `[]`, the real cycle vanished, and swapping
# the two copies' order flipped detection on. Cycle detection must be
# order-independent: union every same-id record's edges (mirror
# serves_resolver.resolve_serves' accumulate+dedupe) so ANY copy declaring
# the cyclic edge surfaces the cycle, never masks it.
# ---------------------------------------------------------------------------

def test_detect_cycles_dup_milestone_id_unions_edges_cyclic_copy_first():
    milestones = [
        {"id": "MS-1", "depends_on": ["MS-2"]},  # cyclic edge on first copy
        {"id": "MS-1", "depends_on": []},        # empty copy last -- must NOT mask
        {"id": "MS-2", "depends_on": ["MS-1"]},
    ]
    assert roadmap_rollup.detect_cycles(milestones) == {"MS-1", "MS-2"}


def test_detect_cycles_dup_milestone_id_order_independent():
    cyclic_first = [
        {"id": "MS-1", "depends_on": ["MS-2"]},
        {"id": "MS-1", "depends_on": []},
        {"id": "MS-2", "depends_on": ["MS-1"]},
    ]
    empty_first = [
        {"id": "MS-1", "depends_on": []},
        {"id": "MS-1", "depends_on": ["MS-2"]},
        {"id": "MS-2", "depends_on": ["MS-1"]},
    ]
    assert roadmap_rollup.detect_cycles(cyclic_first) == roadmap_rollup.detect_cycles(empty_first)


def test_assemble_roadmap_cycle_flag_written_not_crashed(tmp_path):
    proj = _proj(tmp_path)
    specs = [
        {"milestone_id": "MS-1", "depends_on": ["MS-2"]},
        {"milestone_id": "MS-2", "depends_on": ["MS-1"]},
    ]
    written = roadmap_rollup.assemble_roadmap(proj, specs)
    by_id = {m["id"]: m for m in written["milestones"]}
    assert by_id["MS-1"]["cycle"] is True
    assert by_id["MS-2"]["cycle"] is True


# ---------------------------------------------------------------------------
# Write / read round-trip + containment
# ---------------------------------------------------------------------------

def test_write_roadmap_lands_under_shape_dir(tmp_path):
    proj = _proj(tmp_path)
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[])
    target = roadmap_rollup.write_roadmap(proj, [milestone])
    expected = proj / "docs" / "product" / "shape" / "roadmap.md"
    assert target == expected
    assert target.is_file()


def test_read_roadmap_round_trips_milestones(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[t1["id"]])
    roadmap_rollup.write_roadmap(proj, [milestone])

    read_back = roadmap_rollup.read_roadmap(proj)
    assert read_back["milestones"][0]["id"] == "MS-1"
    assert read_back["milestones"][0]["effort_rollup"] == "2d"


def test_read_roadmap_missing_file_returns_empty_not_crashed(tmp_path):
    proj = _proj(tmp_path)
    assert roadmap_rollup.read_roadmap(proj) == {"milestones": []}


def test_read_roadmap_filters_non_string_milestone_id(tmp_path):
    # A hand-edited non-string milestone `id` (e.g. `id: {a: 1}`, valid YAML) is
    # unhashable; detect_cycles keys its graph by id, so an unfiltered entry
    # crashes every read AND poisons the locked read-merge-write add path.
    proj = _proj(tmp_path)
    rm = proj / "docs" / "product" / "shape" / "roadmap.md"
    rm.parent.mkdir(parents=True, exist_ok=True)
    rm.write_text(
        "---\nmilestones:\n  - id: {a: 1}\n    title: bad\n    depends_on: []\n"
        "  - id: MS-1\n    title: good\n    depends_on: []\n"
        "generated_ts: '2026-01-01T00:00:00+00:00'\nactor: t\n---\n# Roadmap\n",
        encoding="utf-8",
    )
    read_back = roadmap_rollup.read_roadmap(proj)          # must not raise
    ids = [m["id"] for m in read_back["milestones"]]
    assert ids == ["MS-1"]                                  # bad entry filtered
    # the whole read->annotate->write path must survive the poisoned input too
    roadmap_rollup.write_roadmap(proj, read_back["milestones"])


def test_roadmap_path_escape_raises(tmp_path):
    with pytest.raises(PermissionError):
        shape_paths.shape_path(tmp_path, "../stories/x.md")


# ---------------------------------------------------------------------------
# Fail-open on a malformed frontmatter YAML: PyYAML's timestamp constructor
# raises a bare ValueError (not yaml.YAMLError) on an out-of-range unquoted
# date; a non-UTF-8 file raises UnicodeDecodeError. read_roadmap() treats a
# malformed sidecar as "never rolled up yet" -- fail-open, never a raw
# parser traceback.
# ---------------------------------------------------------------------------

def test_read_roadmap_bad_date_frontmatter_fails_open_not_crash(tmp_path):
    proj = _proj(tmp_path)
    target = proj / "docs" / "product" / "shape" / "roadmap.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\nmilestones: []\ngenerated_ts: 2026-13-99\n---\n\n# Roadmap\n",
        encoding="utf-8",
    )
    assert roadmap_rollup.read_roadmap(proj) == {"milestones": []}


def test_read_roadmap_yaml_timestamp_tag_frontmatter_fails_open_not_crash(tmp_path):
    """A `!!timestamp` explicit-tag value raises a bare AttributeError from
    PyYAML's construct_yaml_timestamp -- not yaml.YAMLError or ValueError --
    and must still fail open, never a raw parser traceback."""
    proj = _proj(tmp_path)
    target = proj / "docs" / "product" / "shape" / "roadmap.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\nmilestones: []\ngenerated_ts: !!timestamp 'not a ts'\n---\n\n# Roadmap\n",
        encoding="utf-8",
    )
    assert roadmap_rollup.read_roadmap(proj) == {"milestones": []}


def test_read_roadmap_non_utf8_file_fails_open_not_crash(tmp_path):
    proj = _proj(tmp_path)
    target = proj / "docs" / "product" / "shape" / "roadmap.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"---\nmilestones: \xff\xfe bad\n---\n\nbody\n")
    assert roadmap_rollup.read_roadmap(proj) == {"milestones": []}


# ---------------------------------------------------------------------------
# `milestones:` must coerce to [] unless it is already a list of dicts --
# `setdefault` only fills a MISSING key, so a hand-edited bare-string or
# list-of-strings `milestones:` sails through unchanged and later breaks a
# `.get()` call downstream with an AttributeError.
# ---------------------------------------------------------------------------

def test_read_roadmap_bare_string_milestones_coerced_to_empty(tmp_path):
    proj = _proj(tmp_path)
    target = proj / "docs" / "product" / "shape" / "roadmap.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\nmilestones: a bare string, not a list\n---\n\n# Roadmap\n",
        encoding="utf-8",
    )
    assert roadmap_rollup.read_roadmap(proj) == {"milestones": []}


def test_read_roadmap_list_of_strings_milestones_coerced_to_empty(tmp_path):
    proj = _proj(tmp_path)
    target = proj / "docs" / "product" / "shape" / "roadmap.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\nmilestones: [MS-1, MS-2]\n---\n\n# Roadmap\n",
        encoding="utf-8",
    )
    assert roadmap_rollup.read_roadmap(proj) == {"milestones": []}


def test_read_roadmap_list_of_dicts_milestones_passes_through(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[t1["id"]])
    roadmap_rollup.write_roadmap(proj, [milestone])

    read_back = roadmap_rollup.read_roadmap(proj)
    assert isinstance(read_back["milestones"], list)
    assert read_back["milestones"][0]["id"] == "MS-1"


# ---------------------------------------------------------------------------
# `%` binds before `or` -- "- contains: %s" % contains_str is ALWAYS a
# non-empty string (the literal prefix alone makes it truthy), so the
# "(none)" fallback after `or` is dead code; an empty `contains` used to
# render a bare "- contains: " instead.
# ---------------------------------------------------------------------------

def test_render_body_empty_contains_renders_none_not_blank(tmp_path):
    proj = _proj(tmp_path)
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[])
    target = roadmap_rollup.write_roadmap(proj, [milestone])
    text = target.read_text(encoding="utf-8")
    assert "- contains: (none)" in text
    assert "- contains: \n" not in text


# ---------------------------------------------------------------------------
# The "loud" skip for unmapped_sizes/dropped_estimates was only a stored
# record field -- the --add-milestone CLI printed just id/effort/gated, so
# an operator running the CLI never saw the drop unless they went looking
# at the roadmap sidecar by hand. Emit a stderr warning.
# ---------------------------------------------------------------------------

def test_add_milestone_cli_warns_on_dropped_estimates(tmp_path, capsys):
    proj = _proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "serves: [%s]\n"
        "title: t\n"
        "estimate: 2 days\n"
        "depends_on: []\n"
        "acceptance: []\n"
        "status: open\n"
        "---\n\n# TASK-1\n" % _STORY_1,
        encoding="utf-8",
    )
    rc = roadmap_rollup.main([
        "--root", str(proj), "--add-milestone", "--id", "MS-1", "--task-ids", "TASK-1",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning" in err.lower()
    # a size-derived fallback (or nothing) replaced the BA figure -- it was
    # never "dropped from effort_rollup", the BA estimate was ignored
    assert "ignored" in err.lower()
    assert "story size" in err.lower()


def test_add_milestone_cli_silent_when_nothing_dropped_or_unmapped(tmp_path, capsys):
    proj = _proj(tmp_path)
    _task(proj, estimate="2d")
    rc = roadmap_rollup.main([
        "--root", str(proj), "--add-milestone", "--id", "MS-1", "--task-ids", "TASK-1",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert err == ""


# ---------------------------------------------------------------------------
# A hand-edited `contains:[1,2]` (ints instead of task-id strings) must not
# TypeError the write -- coerce/skip non-str entries in the join.
# ---------------------------------------------------------------------------

def test_write_roadmap_contains_int_entries_does_not_crash(tmp_path):
    proj = _proj(tmp_path)
    milestone = {
        "id": "MS-1", "title": "t", "target_window": "",
        "contains": [1, 2], "excluded": [], "effort_rollup": "0d",
        "poc_gated": False, "depends_on": [],
    }
    target = roadmap_rollup.write_roadmap(proj, [milestone])
    text = target.read_text(encoding="utf-8")
    assert "1, 2" in text


# ---------------------------------------------------------------------------
# A bare-int `estimate: 3` (non-string) is dropped from the sum rather than
# silently mis-summing -- and the drop is surfaced, not invisible.
# ---------------------------------------------------------------------------

def test_bare_int_estimate_dropped_and_surfaced(tmp_path):
    proj = _proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "serves: [%s]\n"
        "title: t\n"
        "estimate: 3\n"
        "depends_on: []\n"
        "acceptance: []\n"
        "status: open\n"
        "---\n\n# TASK-1\n" % _STORY_1,
        encoding="utf-8",
    )
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=["TASK-1"])
    assert milestone["effort_rollup"] == "0d"
    assert milestone["dropped_estimates"] == ["TASK-1"]


# ---------------------------------------------------------------------------
# A truthy but UNPARSABLE string estimate ("2 days", "3", "~3d") must also
# land in dropped_estimates -- estimate_for_task() returns the raw string
# as-is (an explicit BA figure wins outright over anything size-derived),
# so it silently rides into `estimates` and then sum_estimates() drops it
# during summation with no record anywhere.
# ---------------------------------------------------------------------------

def test_unparsable_string_estimate_dropped_and_surfaced(tmp_path):
    proj = _proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "serves: [%s]\n"
        "title: t\n"
        "estimate: 2 days\n"
        "depends_on: []\n"
        "acceptance: []\n"
        "status: open\n"
        "---\n\n# TASK-1\n" % _STORY_1,
        encoding="utf-8",
    )
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=["TASK-1"])
    assert milestone["effort_rollup"] == "0d"
    assert milestone["dropped_estimates"] == ["TASK-1"]


def test_unparsable_negative_estimate_falls_back_to_story_size_not_silent_zero(tmp_path):
    proj = _proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "serves: [%s]\n"
        "title: t\n"
        "estimate: \"-2d\"\n"
        "depends_on: []\n"
        "acceptance: []\n"
        "status: open\n"
        "---\n\n# TASK-1\n" % _STORY_1,
        encoding="utf-8",
    )
    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=["TASK-1"], story_sizes={_STORY_1: "M"},
    )
    # The BA's own (unparsable) figure is still ignored/flagged, but effort
    # now leans on the linked story's size range instead of silently zeroing.
    assert milestone["dropped_estimates"] == ["TASK-1"]
    assert milestone["effort_rollup"] == "3-5d"


def test_parsable_string_estimate_not_flagged_as_dropped(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[t1["id"]])
    assert milestone["dropped_estimates"] == []
    assert milestone["effort_rollup"] == "2d"


# ---------------------------------------------------------------------------
# An unmapped story size (not in the size->range table) must be a loud,
# counted skip -- not a silent 0 folded into effort_rollup.
# ---------------------------------------------------------------------------

def test_unmapped_story_size_is_loud_skip_not_silent_zero(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="")

    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=[t1["id"]], story_sizes={_STORY_1: "XL"},
    )

    assert milestone["effort_rollup"] == "0d"
    assert milestone["unmapped_sizes"] == [{"task_id": t1["id"], "size": "XL"}]


def test_mapped_story_size_leaves_unmapped_sizes_empty(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="")

    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=[t1["id"]], story_sizes={_STORY_1: "M"},
    )

    assert milestone["effort_rollup"] == "3-5d"
    assert milestone["unmapped_sizes"] == []


# ---------------------------------------------------------------------------
# closed must coerce via `is True`, not `bool(...)` -- a hand-edited
# `closed: "false"` (a truthy non-empty string) must read as NOT closed.
# ---------------------------------------------------------------------------

def test_poc_closed_string_false_reads_as_not_closed(tmp_path):
    proj = _proj(tmp_path)
    poc = poc_gate.author(proj, subject="x")
    path = Path(poc["path"])
    text = path.read_text(encoding="utf-8")
    assert "closed: false\n" in text
    text = text.replace("closed: false\n", "closed: 'false'\n")
    path.write_text(text, encoding="utf-8")

    assert roadmap_rollup.poc_closed_status(proj, poc["id"]) is False


# ---------------------------------------------------------------------------
# poc_gate_status distinguishes "nothing declared" (advisory) from "a gate
# was declared but is not yet satisfied" (unsatisfied) -- both collapse to
# poc_gated:False, which is ambiguous on its own.
# ---------------------------------------------------------------------------

def test_poc_gate_status_distinguishes_advisory_vs_unsatisfied(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")

    advisory = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[t1["id"]])
    assert advisory["poc_gated"] is False
    assert advisory["poc_gate_status"] == "advisory"

    poc = poc_gate.author(proj, subject="feasibility check still open")
    unsatisfied = roadmap_rollup.build_milestone(
        proj, "MS-2", task_ids=[t1["id"]], task_poc_map={t1["id"]: poc["id"]},
    )
    assert unsatisfied["poc_gated"] is False
    assert unsatisfied["poc_gate_status"] == "unsatisfied"


# ---------------------------------------------------------------------------
# `build_milestone`'s story-size lookup used to `for story_id in serves`
# with no shape check -- a bare-string `serves` char-iterated (risking a
# bogus single-letter story_sizes match), an int `serves` TypeError'd (not
# iterable), and a nested-list serves entry TypeError'd (unhashable in the
# `story_id in story_sizes` membership check). Routed through
# id_grammar.normalize_serves so only real string ids are ever walked.
# ---------------------------------------------------------------------------

def test_build_milestone_bare_string_serves_does_not_crash(tmp_path):
    proj = _proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "serves: STORY-1\n"
        "title: t\n"
        "estimate: ''\n"
        "depends_on: []\n"
        "acceptance: []\n"
        "status: open\n"
        "---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=["TASK-1"], story_sizes={"S": "M"},
    )
    assert milestone["contains"] == ["TASK-1"]
    # a bare (non-list) `serves` must never char-iterate into a bogus
    # single-letter story_sizes match ('S' is a key, but must not "match")
    assert milestone["effort_rollup"] == "0d"


def test_build_milestone_int_serves_does_not_crash(tmp_path):
    proj = _proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "serves: 5\n"
        "title: t\n"
        "estimate: ''\n"
        "depends_on: []\n"
        "acceptance: []\n"
        "status: open\n"
        "---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=["TASK-1"], story_sizes={"5": "M"},
    )
    assert milestone["contains"] == ["TASK-1"]
    assert milestone["effort_rollup"] == "0d"


def test_build_milestone_nested_list_serves_entry_does_not_crash(tmp_path):
    proj = _proj(tmp_path)
    tasks_dir = proj / "docs" / "product" / "shape" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "TASK-1.md").write_text(
        "---\n"
        "id: TASK-1\n"
        "serves: [[x]]\n"
        "title: t\n"
        "estimate: ''\n"
        "depends_on: []\n"
        "acceptance: []\n"
        "status: open\n"
        "---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=["TASK-1"])
    assert milestone["contains"] == ["TASK-1"]
    assert milestone["effort_rollup"] == "0d"


# ---------------------------------------------------------------------------
# adv-correctness (LOW): a caller-supplied `size_table` entry that is itself
# unparsable (e.g. {"S": "2 days"}) used to ride silently into `estimates`
# and get dropped by `sum_estimates` with no record anywhere. The
# size-derived `est` is now validated through `parse_estimate_days` just like
# an explicit task estimate, and flagged into `dropped_estimates` on failure.
# ---------------------------------------------------------------------------

def test_size_table_unparsable_value_dropped_and_surfaced(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="")

    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=[t1["id"]],
        story_sizes={_STORY_1: "S"}, size_table={"S": "2 days"},
    )

    assert milestone["effort_rollup"] == "0d"
    assert milestone["dropped_estimates"] == [t1["id"]]
    assert milestone["unmapped_sizes"] == []


def test_poc_gate_status_satisfied_when_all_declared_gates_close(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    poc = poc_gate.author(proj, subject="feasibility check")
    review = proj / "artifacts" / "review-decision.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text('{"verdict": "PASS"}', encoding="utf-8")
    verification = proj / "artifacts" / "verification.json"
    verification.write_text('{"verdict": "PASS"}', encoding="utf-8")
    poc_gate.gate(proj, poc["id"], review_decision_path=review, verification_path=verification)

    milestone = roadmap_rollup.build_milestone(
        proj, "MS-1", task_ids=[t1["id"]], task_poc_map={t1["id"]: poc["id"]},
    )
    assert milestone["poc_gated"] is True
    assert milestone["poc_gate_status"] == "satisfied"


# ---------------------------------------------------------------------------
# Regression: the PO story file is never touched by a roadmap rollup
# ---------------------------------------------------------------------------

def test_stories_byte_unchanged_after_roadmap_write(tmp_path):
    proj = _proj(tmp_path)
    story_path = proj / "docs" / "product" / "stories" / (_STORY_1 + ".md")
    before = story_path.read_bytes()

    t1 = _task(proj, estimate="2d")
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[t1["id"]])
    roadmap_rollup.write_roadmap(proj, [milestone])

    assert story_path.read_bytes() == before


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_roadmap_schema_valid(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    import json

    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[t1["id"]])
    roadmap_rollup.write_roadmap(proj, [milestone])
    record = roadmap_rollup.read_roadmap(proj)

    schema_path = _SHAPE_SCRIPTS.parent / "schemas" / "roadmap.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=record, schema=schema)


# ---------------------------------------------------------------------------
# View reuse: the existing PO roadmap/time views still render fine after a
# BA rollup exists alongside them -- no second renderer added here.
# ---------------------------------------------------------------------------

def _load_spec_render_pipeline():
    spec_scripts = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
    names = [
        "encoding_utils", "id_grammar", "frontmatter_parser", "spec_graph",
        "render_common", "i18n_labels", "render_ascii",
    ]
    return load_skill_scripts(spec_scripts, names)


def test_po_roadmap_and_time_views_still_render_after_ba_rollup(tmp_path):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")
    milestone = roadmap_rollup.build_milestone(proj, "MS-1", task_ids=[t1["id"]])
    roadmap_rollup.write_roadmap(proj, [milestone])

    spec_mods = _load_spec_render_pipeline()
    graph = spec_mods["spec_graph"].build_graph(proj)

    roadmap_text = spec_mods["render_ascii"].roadmap(graph)
    time_text = spec_mods["render_ascii"].time(graph)

    assert isinstance(roadmap_text, str) and roadmap_text
    assert isinstance(time_text, str) and time_text


def test_build_milestone_coerces_bare_string_depends_on():
    # A programmatic `depends_on="MS-2"` must not char-explode to
    # ['M','S','-','2'] and poison the stored roadmap / cycle detection.
    m = roadmap_rollup.build_milestone(".", "MS-1", depends_on="MS-2")
    assert m["depends_on"] == ["MS-2"]


# ---------------------------------------------------------------------------
# detect_cycles used a plain recursive DFS -- a `depends_on` chain deeper
# than Python's default recursion limit (~1000) raised RecursionError
# instead of degrading gracefully. Reachability is negligible (1000+
# hand-authored milestones), but the walk must not crash outright on one.
# ---------------------------------------------------------------------------

def test_detect_cycles_deep_chain_does_not_recursion_error():
    depth = 3000
    milestones = [{"id": "MS-1", "depends_on": []}]
    milestones += [
        {"id": "MS-%d" % i, "depends_on": ["MS-%d" % (i - 1)]}
        for i in range(2, depth + 1)
    ]
    assert roadmap_rollup.detect_cycles(milestones) == set()


def test_detect_cycles_deep_chain_with_cycle_at_the_end_still_detected():
    depth = 3000
    milestones = [{"id": "MS-1", "depends_on": ["MS-%d" % depth]}]
    milestones += [
        {"id": "MS-%d" % i, "depends_on": ["MS-%d" % (i - 1)]}
        for i in range(2, depth + 1)
    ]
    assert roadmap_rollup.detect_cycles(milestones) == {
        "MS-%d" % i for i in range(1, depth + 1)
    }


# ---------------------------------------------------------------------------
# `--add-milestone` / `add_milestone_locked`'s read -> merge -> write must
# run under an exclusive flock on a sibling `.roadmap.lock` file, mirroring
# `task_model.py`'s `.tasks.lock` idiom -- a bare read_roadmap()+write_roadmap()
# pair with no lock spanning the whole sequence let two concurrent
# --add-milestone calls both read the same `existing` list before either
# wrote, silently clobbering one writer's update.
# ---------------------------------------------------------------------------

def test_add_milestone_locked_acquires_lock_on_roadmap_lock_file(tmp_path, monkeypatch):
    proj = _proj(tmp_path)
    t1 = _task(proj, estimate="2d")

    calls = []
    real_flock = roadmap_rollup.fcntl.flock

    def _spy_flock(fd, op):
        calls.append(op)
        return real_flock(fd, op)

    monkeypatch.setattr(roadmap_rollup.fcntl, "flock", _spy_flock)

    roadmap_rollup.add_milestone_locked(proj, "MS-1", task_ids=[t1["id"]])

    assert roadmap_rollup.fcntl.LOCK_EX in calls
    assert roadmap_rollup.fcntl.LOCK_UN in calls
    lock_path = proj / "docs" / "product" / "shape" / ".roadmap.lock"
    assert lock_path.is_file()


def test_render_body_coerces_bare_string_contains():
    # A hand-edited roadmap.md with `contains: TASK-1` (bare string) must not
    # char-split to "T, A, S, K, -, 1" and be written back.
    out = roadmap_rollup._render_body([{"id": "MS-1", "contains": "TASK-1"}])
    line = [l for l in out.split("\n") if l.strip().startswith("- contains:")][0]
    assert "TASK-1" in line and "T, A, S" not in line


def test_scc_tarjan_deterministic_across_insertion_order():
    # roadmap_rollup._scc_tarjan is a by-hand copy of
    # check_traceability._scc_tarjan, whose docstring promises byte-deterministic
    # output (it iterates `sorted(adj)`). The copy must match: the result cannot
    # depend on dict insertion order, or the "keep the two copies in sync by
    # hand" claim is already false. Same edges, two insertion orders -> identical.
    adj_a = {"A": ["B"], "B": ["A"], "C": []}
    adj_b = {"C": [], "B": ["A"], "A": ["B"]}
    assert roadmap_rollup._scc_tarjan(adj_a) == roadmap_rollup._scc_tarjan(adj_b)
