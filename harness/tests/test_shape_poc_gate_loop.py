"""hs:shape — technical-POC gate + closed-loop plan-intake handoff (BA sidecar).

The BA sidecar closes two loops here:

- `poc_gate.py` READS an already-produced review verdict artifact (and, optionally,
  a paired verification artifact) and records a POC sidecar record from it — it never
  spawns or re-runs a review itself (see test_no_running_code_in_poc_gate /
  test_no_review_spawn_tokens_in_poc_gate below, hard guards on that boundary, not just
  documentation). A POC only reads as closed when BOTH artifacts read back exactly
  PASS; anything else (missing file, BLOCKED, a changed artifact shape) fails open —
  advisory, never a crash — and simply leaves the POC unclosed.
- `loop_handoff.py` renders a plan-intake brief (markdown, never a machine plan-graph)
  from the BA task sidecar, carrying the originating POC id forward so a concluded
  POC can hand back into the ordinary plan/cook/test loop.

Both writers resolve every target through `shape_paths.shape_path()` — the same
containment invariant every other hs:shape script uses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Literal path kept intact for the stashed-skill collect_ignore coupling:
# harness/plugins/hs/skills/shape/scripts
_SHAPE_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "shape" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_mods = load_skill_scripts(
    _SHAPE_SCRIPTS, ["shape_paths", "_sidecar", "task_model", "poc_gate", "loop_handoff"]
)
shape_paths = _mods["shape_paths"]
_sidecar = _mods["_sidecar"]
task_model = _mods["task_model"]
poc_gate = _mods["poc_gate"]
loop_handoff = _mods["loop_handoff"]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _write_verdict_artifact(path: Path, verdict: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"verdict": verdict}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Author POC task
# ---------------------------------------------------------------------------

def test_author_poc_writes_open_sidecar_monotonic(tmp_path):
    record = poc_gate.author(tmp_path, subject="story needing technical verification")
    assert record["id"] == "POC-1"
    assert record["status"] == "open"
    assert record["closed"] is False
    target = tmp_path / "docs" / "product" / "shape" / "poc" / "POC-1.md"
    assert target.is_file()
    assert record["path"] == str(target)

    second = poc_gate.author(tmp_path, subject="second POC")
    assert second["id"] == "POC-2"


# ---------------------------------------------------------------------------
# Gate: verdict read PASS closes; BLOCKED does not
# ---------------------------------------------------------------------------

def test_gate_review_and_verification_pass_closes_poc(tmp_path):
    record = poc_gate.author(tmp_path, subject="a story")
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")
    verification_path = _write_verdict_artifact(tmp_path / "artifacts" / "verification.json", "PASS")

    result = poc_gate.gate(
        tmp_path, record["id"],
        review_decision_path=review_path,
        verification_path=verification_path,
    )

    assert result["verdict"] == "PASS"
    assert result["closed"] is True
    assert result["status"] == "closed"


def test_gate_blocked_review_does_not_close(tmp_path):
    record = poc_gate.author(tmp_path, subject="a story")
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "BLOCKED")

    result = poc_gate.gate(tmp_path, record["id"], review_decision_path=review_path)

    assert result["verdict"] == "BLOCKED"
    assert result["closed"] is False
    assert result["status"] == "open"


def test_gate_review_pass_without_verification_stays_unclosed(tmp_path):
    """review PASS alone is not enough -- verification PASS is also required."""
    record = poc_gate.author(tmp_path, subject="a story")
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")

    result = poc_gate.gate(tmp_path, record["id"], review_decision_path=review_path)

    assert result["verdict"] == "PASS"
    assert result["closed"] is False


def test_gate_verification_blocked_keeps_poc_open_even_with_review_pass(tmp_path):
    record = poc_gate.author(tmp_path, subject="a story")
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")
    verification_path = _write_verdict_artifact(
        tmp_path / "artifacts" / "verification.json", "BLOCKED"
    )

    result = poc_gate.gate(
        tmp_path, record["id"],
        review_decision_path=review_path,
        verification_path=verification_path,
    )

    assert result["closed"] is False


def test_gate_missing_review_decision_file_fails_open_not_crash(tmp_path):
    record = poc_gate.author(tmp_path, subject="a story")
    missing_path = tmp_path / "artifacts" / "does-not-exist.json"

    result = poc_gate.gate(tmp_path, record["id"], review_decision_path=missing_path)

    assert result["verdict"] is None
    assert result["closed"] is False


def test_gate_malformed_verdict_json_fails_open_not_crash(tmp_path):
    record = poc_gate.author(tmp_path, subject="a story")
    bad_path = tmp_path / "artifacts" / "review-decision.json"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("not json at all {{{", encoding="utf-8")

    result = poc_gate.gate(tmp_path, record["id"], review_decision_path=bad_path)

    assert result["verdict"] is None
    assert result["closed"] is False


def test_gate_unknown_poc_id_raises_clear_error(tmp_path):
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")
    with pytest.raises(poc_gate.PocError):
        poc_gate.gate(tmp_path, "POC-999", review_decision_path=review_path)


# ---------------------------------------------------------------------------
# A re-gate that now FAILS must reopen a previously-closed POC -- the old
# `updated.get("status", "open")` fallback kept a prior "closed" status on
# disk even though `closed` itself was False for this re-gate.
# ---------------------------------------------------------------------------

def test_gate_regate_failing_verdict_reopens_closed_poc(tmp_path):
    record = poc_gate.author(tmp_path, subject="a story")
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")
    verification_path = _write_verdict_artifact(
        tmp_path / "artifacts" / "verification.json", "PASS"
    )
    closed = poc_gate.gate(
        tmp_path, record["id"],
        review_decision_path=review_path, verification_path=verification_path,
    )
    assert closed["status"] == "closed"

    fail_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "BLOCKED")
    regated = poc_gate.gate(
        tmp_path, record["id"],
        review_decision_path=fail_path, verification_path=verification_path,
    )
    assert regated["closed"] is False
    assert regated["status"] == "open"

    # persisted, not just returned in-memory
    fm, _body = poc_gate.read_poc(tmp_path, record["id"])
    assert fm["status"] == "open"


# ---------------------------------------------------------------------------
# The verdict enum is read from the shipped harness schema SSOT
# (harness/schemas/artifact-review-decision.json), not hand-copied; an
# artifact carrying a verdict value outside that set is surfaced (not
# silently folded into the same bucket as a missing artifact).
# ---------------------------------------------------------------------------

def test_known_verdicts_sourced_from_harness_schema():
    import json

    schema_path = ROOT / "harness" / "schemas" / "artifact-review-decision.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert set(poc_gate._KNOWN_VERDICTS) == set(schema["properties"]["verdict"]["enum"])


def test_gate_unrecognized_verdict_value_surfaced_not_silently_unclosed(tmp_path):
    """A review-decision artifact carrying a verdict value outside the known
    set (e.g. a rename/typo on the harness side, 'APPROVED' instead of
    'PASS') must not collapse into the exact same shape as a missing/absent
    artifact -- gate() surfaces it via verdict_unknown."""
    record = poc_gate.author(tmp_path, subject="a story")
    review_path = _write_verdict_artifact(
        tmp_path / "artifacts" / "review-decision.json", "APPROVED"
    )

    result = poc_gate.gate(tmp_path, record["id"], review_decision_path=review_path)

    assert result["verdict"] is None
    assert result["closed"] is False
    assert result["verdict_unknown"] is True


def test_gate_known_verdict_never_flags_unknown(tmp_path):
    record = poc_gate.author(tmp_path, subject="a story")
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")

    result = poc_gate.gate(tmp_path, record["id"], review_decision_path=review_path)

    assert result["verdict_unknown"] is False


# ---------------------------------------------------------------------------
# Concurrency: gate()'s read-modify-write runs under the same .poc.lock
# flock author() already takes.
# ---------------------------------------------------------------------------

def test_gate_acquires_lock_on_poc_lock_file(tmp_path, monkeypatch):
    record = poc_gate.author(tmp_path, subject="a story")
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")

    calls = []
    real_flock = poc_gate.fcntl.flock

    def _spy_flock(fd, op):
        calls.append(op)
        return real_flock(fd, op)

    monkeypatch.setattr(poc_gate.fcntl, "flock", _spy_flock)

    poc_gate.gate(tmp_path, record["id"], review_decision_path=review_path)

    assert poc_gate.fcntl.LOCK_EX in calls
    assert poc_gate.fcntl.LOCK_UN in calls
    lock_path = tmp_path / "docs" / "product" / "shape" / "poc" / ".poc.lock"
    assert lock_path.is_file()


# ---------------------------------------------------------------------------
# The closure literal "PASS" must itself be a member of the schema-sourced
# _KNOWN_VERDICTS -- a harness verdict rename that drops "PASS" must fail
# loudly (raise at import time via the same guard this test calls
# directly), never leave `closed` silently and permanently False.
# ---------------------------------------------------------------------------

def test_success_sentinel_is_a_known_verdict():
    assert poc_gate._SUCCESS_VERDICT in poc_gate._KNOWN_VERDICTS
    assert poc_gate._SUCCESS_VERDICT == "PASS"


def test_assert_success_sentinel_raises_when_schema_drops_pass():
    """The exact guard poc_gate runs at import time: handed a verdict tuple
    that lost "PASS" (a hypothetical harness rename), it must raise loudly
    rather than let `gate()`'s `== "PASS"` comparison quietly never match."""
    with pytest.raises(RuntimeError):
        poc_gate._assert_success_sentinel(("PASS_WITH_RISK", "BLOCKED"))


def test_assert_success_sentinel_accepts_verdicts_containing_pass():
    assert poc_gate._assert_success_sentinel(("PASS", "PASS_WITH_RISK", "BLOCKED")) == "PASS"


# ---------------------------------------------------------------------------
# Fail-open on a malformed frontmatter YAML: PyYAML's timestamp constructor
# raises a bare ValueError (not yaml.YAMLError) on an out-of-range unquoted
# date; a non-UTF-8 file raises UnicodeDecodeError. Both must surface as a
# clear PocError, never a raw parser traceback.
# ---------------------------------------------------------------------------

def _poc_dir(proj: Path) -> Path:
    d = proj / "docs" / "product" / "shape" / "poc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_read_poc_bad_date_frontmatter_fails_open_not_crash(tmp_path):
    (_poc_dir(tmp_path) / "POC-1.md").write_text(
        "---\n"
        "id: POC-1\n"
        "subject: s\n"
        "title: t\n"
        "plan_id: null\n"
        "status: open\n"
        "verdict: null\n"
        "verification_verdict: null\n"
        "closed: false\n"
        "review_decision_path: null\n"
        "verification_path: null\n"
        "ts: 2026-13-99\n"
        "---\n\n# POC-1\n",
        encoding="utf-8",
    )
    with pytest.raises(poc_gate.PocError):
        poc_gate.read_poc(tmp_path, "POC-1")


def test_read_poc_yaml_timestamp_tag_frontmatter_fails_open_not_crash(tmp_path):
    """A `!!timestamp` explicit-tag value raises a bare AttributeError from
    PyYAML's construct_yaml_timestamp -- not yaml.YAMLError or ValueError --
    and must still fail open, never a raw parser traceback."""
    (_poc_dir(tmp_path) / "POC-1.md").write_text(
        "---\n"
        "id: POC-1\n"
        "subject: s\n"
        "title: t\n"
        "plan_id: null\n"
        "status: open\n"
        "verdict: null\n"
        "verification_verdict: null\n"
        "closed: false\n"
        "review_decision_path: null\n"
        "verification_path: null\n"
        "ts: !!timestamp 'not a ts'\n"
        "---\n\n# POC-1\n",
        encoding="utf-8",
    )
    with pytest.raises(poc_gate.PocError):
        poc_gate.read_poc(tmp_path, "POC-1")


def test_read_poc_non_utf8_file_fails_open_not_crash(tmp_path):
    (_poc_dir(tmp_path) / "POC-1.md").write_bytes(
        b"---\nid: POC-1\nsubject: \xff\xfe bad\n---\n\nbody\n"
    )
    with pytest.raises(poc_gate.PocError):
        poc_gate.read_poc(tmp_path, "POC-1")


# ---------------------------------------------------------------------------
# list_pocs must never surface a raw traceback over one malformed
# hand-edited record -- skip it and keep listing the rest.
# ---------------------------------------------------------------------------

def test_list_pocs_skips_malformed_record_not_crash(tmp_path):
    good = poc_gate.author(tmp_path, subject="good")
    (_poc_dir(tmp_path) / "POC-2.md").write_text(
        "---\nid: POC-2\nts: 2026-13-99\n---\n\nbad\n", encoding="utf-8",
    )
    listed = poc_gate.list_pocs(tmp_path)
    assert [p["id"] for p in listed] == [good["id"]]


# ---------------------------------------------------------------------------
# poc.schema.json backs the POC-<n>.md frontmatter, matching the
# task/experiment schema style.
# ---------------------------------------------------------------------------

def test_poc_schema_valid_at_author(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    import json

    record = poc_gate.author(tmp_path, subject="feasibility check")
    schema_path = _SHAPE_SCRIPTS.parent / "schemas" / "poc.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=record, schema=schema)


def test_poc_schema_valid_after_gate(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    import json

    record = poc_gate.author(tmp_path, subject="feasibility check")
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")
    verification_path = _write_verdict_artifact(
        tmp_path / "artifacts" / "verification.json", "PASS"
    )
    gated = poc_gate.gate(
        tmp_path, record["id"],
        review_decision_path=review_path, verification_path=verification_path,
    )
    schema_path = _SHAPE_SCRIPTS.parent / "schemas" / "poc.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=gated, schema=schema)


# ---------------------------------------------------------------------------
# POC <-> plan_id traceability (both directions)
# ---------------------------------------------------------------------------

def test_gate_records_plan_id_on_poc_sidecar(tmp_path):
    record = poc_gate.author(tmp_path, subject="a story")
    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")
    verification_path = _write_verdict_artifact(
        tmp_path / "artifacts" / "verification.json", "PASS"
    )

    result = poc_gate.gate(
        tmp_path, record["id"],
        review_decision_path=review_path,
        verification_path=verification_path,
        plan_id="plans/260101-0000-example-feature",
    )

    assert result["plan_id"] == "plans/260101-0000-example-feature"
    fm, _body = poc_gate.read_poc(tmp_path, record["id"])
    assert fm["plan_id"] == "plans/260101-0000-example-feature"


def test_author_before_plan_id_known_fails_open_no_crash(tmp_path):
    """A POC is authored before its verifying plan exists yet -- plan_id is
    unknown at author time and must not raise; it is filled in later by gate()."""
    record = poc_gate.author(tmp_path, subject="a story")
    assert record["plan_id"] is None

    review_path = _write_verdict_artifact(tmp_path / "artifacts" / "review-decision.json", "PASS")
    result = poc_gate.gate(tmp_path, record["id"], review_decision_path=review_path)
    assert result["plan_id"] is None  # still unknown -- gate() did not crash either


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def test_list_pocs_sorted_by_numeric_id(tmp_path):
    poc_gate.author(tmp_path, subject="first")
    poc_gate.author(tmp_path, subject="second")
    listed = poc_gate.list_pocs(tmp_path)
    assert [p["id"] for p in listed] == ["POC-1", "POC-2"]


def test_list_pocs_reads_zero_padded_filename(tmp_path):
    # A hand-authored zero-padded `POC-01.md` still matches the file regex, so
    # `--list` must surface it -- re-deriving a canonical `POC-<num>.md` path and
    # looking THAT up silently drops the file that is actually on disk (the same
    # class-fix applied to task_model.list_tasks / experiment_spec.list_experiments).
    record = poc_gate.author(tmp_path, subject="a story")  # writes POC-1.md
    poc = tmp_path / "docs" / "product" / "shape" / "poc"
    (poc / "POC-1.md").rename(poc / "POC-01.md")

    listed = poc_gate.list_pocs(tmp_path)

    assert len(listed) == 1, "zero-padded POC-01.md dropped from --list"
    assert listed[0]["id"] == record["id"]


# ---------------------------------------------------------------------------
# Containment: WRITE only under docs/product/shape/poc/
# ---------------------------------------------------------------------------

def test_poc_write_lands_under_shape_poc_dir(tmp_path):
    record = poc_gate.author(tmp_path, subject="a story")
    expected = tmp_path / "docs" / "product" / "shape" / "poc" / "POC-1.md"
    assert Path(record["path"]) == expected


def test_poc_dir_escape_raises(tmp_path):
    with pytest.raises(PermissionError):
        shape_paths.shape_path(tmp_path, "../stories/x.md")


# ---------------------------------------------------------------------------
# Hard guard: no re-run/spawn of review inside poc_gate.py -- READ-only
# ---------------------------------------------------------------------------

_FORBIDDEN_RUNNING_TOKENS = ("subprocess", "urllib", "requests", "http.client", "socket.")
_FORBIDDEN_SPAWN_TOKENS = ("Task(", "code_review", "code-review.py")


def test_no_running_code_in_poc_gate():
    src = (_SHAPE_SCRIPTS / "poc_gate.py").read_text(encoding="utf-8")
    for token in _FORBIDDEN_RUNNING_TOKENS:
        assert token not in src, f"poc_gate.py contains forbidden running-code token: {token}"


def test_no_review_spawn_tokens_in_poc_gate():
    src = (_SHAPE_SCRIPTS / "poc_gate.py").read_text(encoding="utf-8")
    for token in _FORBIDDEN_SPAWN_TOKENS:
        assert token not in src, f"poc_gate.py contains a review-spawn token: {token}"


def test_no_orchestrator_import_in_poc_gate():
    src = (_SHAPE_SCRIPTS / "poc_gate.py").read_text(encoding="utf-8")
    assert "import orchestrator" not in src
    assert "from orchestrator" not in src


# ---------------------------------------------------------------------------
# loop_handoff: plan-intake brief carries the POC id
# ---------------------------------------------------------------------------

def _author_two_tasks(root):
    task_model.author(
        root, serves=["PRD-AUTH-E1-S1"], title="Build sign-in form",
        depends_on=[], acceptance=["form renders", "submit posts to /login"],
    )
    task_model.author(
        root, serves=["PRD-AUTH-E1-S1"], title="Wire backend session",
        depends_on=["TASK-1"], acceptance=["session cookie set on 200"],
    )


def test_write_brief_carries_poc_id_and_task_fields(tmp_path):
    _author_two_tasks(tmp_path)
    target = loop_handoff.write_brief_from_dir(tmp_path, poc_id="POC-1")
    text = target.read_text(encoding="utf-8")

    assert "poc: POC-1" in text
    assert "TASK-1" in text and "TASK-2" in text
    assert "Build sign-in form" in text
    assert "session cookie set on 200" in text
    assert "TASK-1" in text.split("depends_on:")[2]  # TASK-2's depends_on is rendered


def test_write_brief_without_poc_id_omits_poc_key(tmp_path):
    _author_two_tasks(tmp_path)
    target = loop_handoff.write_brief_from_dir(tmp_path)
    text = target.read_text(encoding="utf-8")
    assert "poc:" not in text


def test_write_brief_no_tasks_raises_clear_error(tmp_path):
    with pytest.raises(loop_handoff.LoopHandoffError):
        loop_handoff.write_brief_from_dir(tmp_path, poc_id="POC-1")


def test_write_brief_output_is_markdown_never_plan_graph_yaml(tmp_path):
    _author_two_tasks(tmp_path)
    target = loop_handoff.write_brief_from_dir(tmp_path, poc_id="POC-1")

    assert target.suffix == ".md"
    shape_root = tmp_path / "docs" / "product" / "shape"
    yaml_hits = list(shape_root.rglob("plan-graph.yaml"))
    assert yaml_hits == []


def test_write_brief_lands_under_shape_dir_via_containment(tmp_path):
    _author_two_tasks(tmp_path)
    target = loop_handoff.write_brief_from_dir(tmp_path, poc_id="POC-1")
    shape_root = (tmp_path / "docs" / "product" / "shape").resolve()
    target.relative_to(shape_root)  # raises ValueError if escaped -- pytest fails the test


# ---------------------------------------------------------------------------
# loop_handoff: a hand-edited task's `serves` can be any YAML shape (a bare
# string, a YAML-auto-resolved date, a bare int) -- the brief renderer must
# read it through the same `id_grammar.normalize_serves` the PO gate and the
# BA resolver already share, not a bare `", ".join(serves)` that
# char-iterates a bare string and TypeErrors on a non-iterable.
# ---------------------------------------------------------------------------

def _brief_tasks_dir(root: Path) -> Path:
    d = root / "docs" / "product" / "shape" / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_write_brief_bare_string_serves_renders_clean_no_crash(tmp_path):
    (_brief_tasks_dir(tmp_path) / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: STORY-1\ntitle: t\ndepends_on: []\n"
        "acceptance: []\nstatus: open\n---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    target = loop_handoff.write_brief_from_dir(tmp_path)
    text = target.read_text(encoding="utf-8")
    assert "TASK-1" in text
    assert "STORY-1" in text  # the malformed value is surfaced, not swallowed


def test_write_brief_date_entry_serves_renders_clean_no_crash(tmp_path):
    (_brief_tasks_dir(tmp_path) / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: [2026-07-13]\ntitle: t\ndepends_on: []\n"
        "acceptance: []\nstatus: open\n---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    target = loop_handoff.write_brief_from_dir(tmp_path)
    text = target.read_text(encoding="utf-8")
    assert "TASK-1" in text
    assert "2026-07-13" in text


def test_write_brief_int_serves_renders_clean_no_crash(tmp_path):
    (_brief_tasks_dir(tmp_path) / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: 5\ntitle: t\ndepends_on: []\n"
        "acceptance: []\nstatus: open\n---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    target = loop_handoff.write_brief_from_dir(tmp_path)
    text = target.read_text(encoding="utf-8")
    assert "TASK-1" in text


def test_write_brief_bare_string_depends_on_and_acceptance_no_crash(tmp_path):
    # depends_on / acceptance are list-ish fields too: a hand-edited bare string
    # must not char-split into per-letter bullets, and a scalar must not crash.
    (_brief_tasks_dir(tmp_path) / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: []\ntitle: t\ndepends_on: TASK-2\n"
        "acceptance: do the thing\nstatus: open\n---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    target = loop_handoff.write_brief_from_dir(tmp_path)
    text = target.read_text(encoding="utf-8")
    assert "TASK-2" in text                 # the dep is surfaced whole
    assert "T, A, S, K" not in text         # not char-split
    assert "- do the thing" in text         # one bullet, not per-character


def test_write_brief_int_depends_on_and_acceptance_no_crash(tmp_path):
    (_brief_tasks_dir(tmp_path) / "TASK-1.md").write_text(
        "---\nid: TASK-1\nserves: []\ntitle: t\ndepends_on: 5\n"
        "acceptance: 7\nstatus: open\n---\n\n# TASK-1\n",
        encoding="utf-8",
    )
    target = loop_handoff.write_brief_from_dir(tmp_path)  # must not raise
    assert "TASK-1" in target.read_text(encoding="utf-8")


def test_write_brief_locks_and_disambiguates_same_second(tmp_path, monkeypatch):
    # The brief filename is second-precision wall-clock. Without a lock + a
    # uniqueness guard, two briefs authored in the same second land on the same
    # path and the second silently clobbers the first (both callers get a
    # success + a path — real data loss). Freeze the clock so both calls compute
    # the same base ts, and assert: flock is taken, the two briefs land on
    # DISTINCT paths, and each keeps its own poc linkage.
    _author_two_tasks(tmp_path)
    monkeypatch.setattr(loop_handoff, "_now_ts", lambda: "20260101T000000Z")
    calls = []
    real_flock = loop_handoff.fcntl.flock
    monkeypatch.setattr(loop_handoff.fcntl, "flock",
                        lambda fd, op: (calls.append(op), real_flock(fd, op))[1])

    p1 = loop_handoff.write_brief_from_dir(tmp_path, poc_id="POC-1")
    p2 = loop_handoff.write_brief_from_dir(tmp_path, poc_id="POC-2")

    assert loop_handoff.fcntl.LOCK_EX in calls and loop_handoff.fcntl.LOCK_UN in calls
    assert p1 != p2, "same-second briefs must not collide on one path"
    assert p1.exists() and p2.exists()
    assert "poc: POC-1" in p1.read_text(encoding="utf-8")
    assert "poc: POC-2" in p2.read_text(encoding="utf-8")
    lock_path = tmp_path / "docs" / "product" / "shape" / ".plan-intake.lock"
    assert lock_path.is_file()


def test_brief_body_strips_terminal_escapes(tmp_path):
    # A free-text field (acceptance item) carrying a raw ANSI/OSC/bidi sequence
    # must not ride into the brief BODY, where a developer who `cat`s it would
    # have it executed by their terminal. Tasks are passed directly (the on-disk
    # read path drops a raw-C0 file at YAML-parse anyway; this exercises the
    # render\u2192write body strip for both C0 and the valid-YAML bidi vector).
    tasks = [{
        "id": "TASK-1", "serves": [], "depends_on": [],
        "acceptance": ["ok\x1b]0;PWNED\x07\x1b[2J done\u202eEVIL\u2069"],
    }]
    text = loop_handoff.write_brief(tmp_path, tasks).read_text(encoding="utf-8")
    assert "\x1b" not in text and "\x07" not in text
    assert "\u202e" not in text and "\u2069" not in text


def test_sidecar_render_file_strips_control_and_bidi_from_body():
    # The shared _render_file chokepoint (task_model/poc_gate/experiment_spec/
    # roadmap_rollup all route bodies through it) must strip C0/DEL + bidi from
    # the plain-markdown body — the frontmatter is yaml-escaped, the body is not.
    out = _sidecar._render_file(
        {"id": "TASK-1"}, "line\x1b[2J\x07 mid\u202eEVIL\u2069 end"
    )
    assert "\x1b" not in out and "\x07" not in out
    assert "\u202e" not in out and "\u2069" not in out
    assert "mid" in out and "EVIL" in out and "end" in out


def test_task_author_body_strips_terminal_escapes(tmp_path):
    # End-to-end: authoring a task with a hostile title leaves no raw escape in
    # the written sidecar file (frontmatter yaml-escaped, body stripped).
    task_model.author(
        tmp_path, serves=["PRD-AUTH-E1-S1"],
        title="Legit\x1b]0;PWNED\x07\x1b[2J title\u202eEVIL\u2069",
        depends_on=[], acceptance=["ok"],
    )
    written = (task_model.tasks_dir(tmp_path) / "TASK-1.md").read_text(encoding="utf-8")
    assert "\x1b" not in written and "\x07" not in written
    assert "\u202e" not in written and "\u2069" not in written


def test_sidecar_write_record_atomic_no_partial_on_swap_failure(tmp_path, monkeypatch):
    # The shared sidecar write chokepoint must write ATOMICALLY (temp + os.replace)
    # so a concurrent reader of the fixed sidecar path never sees a torn/empty file.
    # Force the swap to fail: a correct impl raises and leaves NO partial file and
    # NO leftover temp. (A bare write_text never calls os.replace, so it would
    # silently succeed here — this is the red/green discriminator.)
    import os as _os
    target = tmp_path / "rec.md"

    def _boom(*_a, **_k):
        raise OSError("swap failed")

    monkeypatch.setattr(_os, "replace", _boom)
    with pytest.raises(OSError):
        _sidecar.write_record(target, {"id": "X"}, "body text")
    assert not target.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_task_author_routes_through_atomic_write(tmp_path, monkeypatch):
    # End-to-end: a shape writer (task author) must go through the atomic path.
    # With os.replace forced to fail, author raises (proving it calls os.replace)
    # and leaves no orphaned temp. Old bare-write_text code would NOT raise.
    import os as _os

    def _boom(*_a, **_k):
        raise OSError("swap failed")

    monkeypatch.setattr(_os, "replace", _boom)
    with pytest.raises(OSError):
        task_model.author(tmp_path, serves=["PRD-AUTH-E1-S1"], title="x",
                          depends_on=[], acceptance=["ok"])
    tdir = task_model.tasks_dir(tmp_path)
    if tdir.exists():
        assert not [p for p in tdir.iterdir() if p.name.endswith(".tmp")]


def test_task_frontmatter_bidi_reserved_word_roundtrips_as_string(tmp_path):
    # A frontmatter value that collapses to a YAML reserved scalar AFTER the
    # bidi/control strip (RLO + "true") must round-trip back as the STRING the
    # author typed, never retyped to a bool/None/int. The strip must run on the
    # record VALUES before yaml.safe_dump, so PyYAML decides quoting on the
    # neutralized string (`title: 'true'`). Stripping the dumped TEXT afterward
    # would leave a bare `title: true` that re-parses as the boolean True.
    task_model.author(
        tmp_path, serves=["PRD-AUTH-E1-S1"],
        title="\u202etrue", depends_on=[], acceptance=["ok"],
    )
    written = (task_model.tasks_dir(tmp_path) / "TASK-1.md").read_text(encoding="utf-8")
    assert "\u202e" not in written  # bidi neutralized in the frontmatter
    t = next(t for t in task_model.list_tasks(tmp_path) if t["id"] == "TASK-1")
    assert t["title"] == "true"           # the string, not the boolean
    assert isinstance(t["title"], str)


# ---------------------------------------------------------------------------
# CLI --poc contract: the brief's whole trace-back promise (module docstring)
# is "once a POC has gated CLOSED, cite it". The CLI must therefore refuse a
# --poc that is missing, malformed, or still open/BLOCKED, rather than silently
# writing a brief that cites a POC which never closed (a fake closed loop). The
# validation lives at the CLI edge (main); the library render/write helpers stay
# permissive builders (callers there pass their own already-vetted poc ids).
# ---------------------------------------------------------------------------

def test_cli_rejects_poc_not_gated_closed(tmp_path, capsys):
    _author_two_tasks(tmp_path)
    poc_gate.author(tmp_path, subject="feasibility")
    review_path = _write_verdict_artifact(
        tmp_path / "artifacts" / "review-decision.json", "BLOCKED"
    )
    poc_gate.gate(tmp_path, "POC-1", review_decision_path=review_path)  # closed=False

    rc = loop_handoff.main(["--root", str(tmp_path), "--poc", "POC-1"])

    assert rc == 1
    assert "POC-1" in capsys.readouterr().err
    briefs = list((tmp_path / "docs" / "product" / "shape").glob("plan-intake-*.md"))
    assert briefs == []


def test_cli_rejects_nonexistent_poc(tmp_path, capsys):
    _author_two_tasks(tmp_path)
    rc = loop_handoff.main(["--root", str(tmp_path), "--poc", "POC-999"])
    assert rc == 1
    assert "POC-999" in capsys.readouterr().err
    briefs = list((tmp_path / "docs" / "product" / "shape").glob("plan-intake-*.md"))
    assert briefs == []


def test_cli_accepts_closed_poc_and_writes_brief(tmp_path, capsys):
    _author_two_tasks(tmp_path)
    poc_gate.author(tmp_path, subject="feasibility")
    review_path = _write_verdict_artifact(
        tmp_path / "artifacts" / "review-decision.json", "PASS"
    )
    verification_path = _write_verdict_artifact(
        tmp_path / "artifacts" / "verification.json", "PASS"
    )
    poc_gate.gate(
        tmp_path, "POC-1",
        review_decision_path=review_path, verification_path=verification_path,
    )  # closed=True

    rc = loop_handoff.main(["--root", str(tmp_path), "--poc", "POC-1"])

    assert rc == 0
    out_path = capsys.readouterr().out.strip()
    assert "poc: POC-1" in Path(out_path).read_text(encoding="utf-8")


def test_cli_without_poc_writes_brief(tmp_path, capsys):
    _author_two_tasks(tmp_path)
    rc = loop_handoff.main(["--root", str(tmp_path)])
    assert rc == 0
    out_path = capsys.readouterr().out.strip()
    assert Path(out_path).is_file()


def test_brief_frontmatter_bidi_stripped(tmp_path):
    # loop_handoff's brief frontmatter (poc id / task ids) was never neutralized
    # \u2014 safe_dump escapes C0 but leaves a Unicode bidi Cf char LITERAL under
    # allow_unicode. A bidi-bearing --poc id would ride raw into the frontmatter.
    # The pre-dump value strip closes that with no type corruption.
    tasks = [{"id": "TASK-1", "serves": [], "depends_on": [], "acceptance": ["ok"]}]
    text = loop_handoff.write_brief(
        tmp_path, tasks, poc_id="\u202ePOC-1"
    ).read_text(encoding="utf-8")
    assert "\u202e" not in text and "POC-1" in text


def test_cli_rejects_poc_with_truthy_string_closed(tmp_path, capsys):
    # A hand-edited `closed: 'false'` (a truthy NON-empty string, genuinely not
    # closed) must read as NOT closed — matching the strict `is True` twin in
    # roadmap_rollup.poc_closed_status. A loose `not fm.get("closed")` would let
    # the string slip and cite an unclosed POC in the brief.
    _author_two_tasks(tmp_path)
    poc_gate.author(tmp_path, subject="feasibility")
    poc_file = tmp_path / "docs" / "product" / "shape" / "poc" / "POC-1.md"
    poc_file.write_text(
        poc_file.read_text(encoding="utf-8").replace("closed: false", "closed: 'false'"),
        encoding="utf-8",
    )
    rc = loop_handoff.main(["--root", str(tmp_path), "--poc", "POC-1"])
    assert rc == 1
    assert "POC-1" in capsys.readouterr().err
    briefs = list((tmp_path / "docs" / "product" / "shape").glob("plan-intake-*.md"))
    assert briefs == []


# ---------------------------------------------------------------------------
# A hand-edited sidecar KEY carrying a bidi/control char (Trojan-Source class)
# collapses onto a real key after stripping — strip_control deliberately raises
# to refuse silently dropping the field. That raise must surface as the module's
# own clean CLI error, never a raw traceback from render_common (three files
# removed from the CLI the operator invoked). Bidi in a VALUE is already covered
# above; the KEY case is the gap the shared write chokepoint left open.
_BIDI_KEY_RECORD = {"id": "POC-1", "title": "real", "titl‮e": "trojan",
                    "status": "open"}


def test_sidecar_write_record_key_collision_raises_typed_error(tmp_path):
    # The shared chokepoint must translate strip_control's bare ValueError into a
    # typed SidecarError (a ValueError subclass) so every hs:shape writer's CLI can
    # catch it — not let a bare ValueError escape the domain error funnel.
    with pytest.raises(_sidecar.SidecarError):
        _sidecar.write_record(tmp_path / "POC-1.md", _BIDI_KEY_RECORD, "body")


def test_poc_gate_cli_key_collision_is_clean_error_not_traceback(tmp_path):
    # End-to-end: --gate on a POC whose hand-edited frontmatter has a bidi-key
    # collision must exit 1 with a clean "error: ..." line, NOT a Python traceback
    # pointing at render_common. gate() writes unconditionally, so a missing
    # review-decision still reaches the write path.
    import subprocess
    poc = tmp_path / "docs" / "product" / "shape" / "poc"
    poc.mkdir(parents=True)
    (poc / "POC-1.md").write_text(
        "---\nid: POC-1\ntitle: real\n\"titl‮e\": trojan\nstatus: open\n---\n# b\n",
        encoding="utf-8")
    script = _SHAPE_SCRIPTS / "poc_gate.py"
    out = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--gate",
         "--id", "POC-1", "--review-decision", str(tmp_path / "nope.json")],
        capture_output=True, text=True)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "Traceback" not in out.stderr, out.stderr
    assert "error:" in out.stderr
