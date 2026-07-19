"""hs:shape — experiment spec author + verdict read (the verification-tiering
rule, "kẹp 2 đầu" — clamp both ends).

The verification-tiering rule (hard): experiment RUNNING (khách thích/trả tiền, đo số thật) is market
territory, owned by the PO OUTSIDE the harness. `experiment_spec.py` only AUTHORS
the pre-registered spec (hypothesis/linked_to/design/success_metric/decision_rule);
`experiment_verdict.py` only READS a metric result the PO supplies and applies the
spec's own `decision_rule` deterministically. Neither script fetches, polls, or
subprocesses anything — see test_no_running_code_in_scripts /
test_no_orchestrator_import below, which are hard guards on that boundary, not
just documentation.
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

_mods = load_skill_scripts(_SHAPE_SCRIPTS, ["experiment_spec", "experiment_verdict"])
experiment_spec = _mods["experiment_spec"]
experiment_verdict = _mods["experiment_verdict"]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_DECISION_RULE = {
    "direction": "higher",
    "target": 10.0,
    "hit_floor": 0.9,
    "partial_floor": 0.5,
}


def _seed_graph(root: Path) -> None:
    """A minimal docs/product/ tree with one BRD goal so linked_to has something
    real to resolve against (spec_graph.build_graph reads this)."""
    product = root / "docs" / "product"
    product.mkdir(parents=True, exist_ok=True)
    (product / "brd.md").write_text(
        "---\n"
        "id: BRD\n"
        "type: brd\n"
        "goals:\n"
        "  - id: BRD-G1\n"
        "    title: Grow signups\n"
        "    metrics: [signup-conversion]\n"
        "---\n\n"
        "# BRD\n",
        encoding="utf-8",
    )


def _author(root: Path, **overrides):
    kwargs = dict(
        hypothesis="A shorter signup form increases conversion.",
        linked_to=["BRD-G1"],
        design={"method": "A/B", "control": "long form", "variant": "short form"},
        success_metric="signup-conversion",
        decision_rule=dict(_DECISION_RULE),
    )
    kwargs.update(overrides)
    return experiment_spec.author(root, **kwargs)


# ---------------------------------------------------------------------------
# Author EXP
# ---------------------------------------------------------------------------

def test_author_writes_draft_status(tmp_path):
    _seed_graph(tmp_path)
    record = _author(tmp_path)
    assert record["id"] == "EXP-1"
    assert record["status"] == "draft"
    assert record["verdict"] is None
    target = tmp_path / "docs" / "product" / "shape" / "experiments" / "EXP-1.md"
    assert target.is_file()
    assert record["path"] == str(target)


def test_author_schema_valid(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    import json

    _seed_graph(tmp_path)
    record = _author(tmp_path)
    schema_path = _SHAPE_SCRIPTS.parent / "schemas" / "experiment.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=record, schema=schema)


def test_author_monotonic_alloc(tmp_path):
    _seed_graph(tmp_path)
    first = _author(tmp_path)
    second = _author(tmp_path, hypothesis="Second hypothesis text.")
    assert first["id"] == "EXP-1"
    assert second["id"] == "EXP-2"


def test_author_alloc_continues_from_existing_files(tmp_path):
    _seed_graph(tmp_path)
    _author(tmp_path)
    _author(tmp_path, hypothesis="Second hypothesis text.")
    third = _author(tmp_path, hypothesis="Third hypothesis text.")
    assert third["id"] == "EXP-3"


# ---------------------------------------------------------------------------
# linked_to dangling
# ---------------------------------------------------------------------------

def test_linked_to_dangling_is_flagged(tmp_path):
    _seed_graph(tmp_path)
    record = _author(tmp_path, linked_to=["BRD-G1", "PRD-GHOST"])
    assert record["dangling_linked_to"] == ["PRD-GHOST"]
    # a resolvable id must NOT be flagged
    assert "BRD-G1" not in record["dangling_linked_to"]


def test_linked_to_all_resolve_when_valid(tmp_path):
    _seed_graph(tmp_path)
    record = _author(tmp_path, linked_to=["BRD-G1"])
    assert record["dangling_linked_to"] == []


# ---------------------------------------------------------------------------
# Verdict pass / fail / determinism
# ---------------------------------------------------------------------------

def test_verdict_pass_when_actual_meets_hit_floor(tmp_path):
    _seed_graph(tmp_path)
    record = _author(tmp_path)
    result = experiment_verdict.apply_verdict(tmp_path, record["id"], actual=10.0)
    assert result["verdict"] == "hit"
    assert result["status"] == "concluded"


def test_verdict_fail_when_actual_below_partial_floor(tmp_path):
    _seed_graph(tmp_path)
    record = _author(tmp_path)
    result = experiment_verdict.apply_verdict(tmp_path, record["id"], actual=1.0)
    assert result["verdict"] == "miss"
    assert result["status"] == "concluded"


def test_verdict_partial_between_floors(tmp_path):
    _seed_graph(tmp_path)
    record = _author(tmp_path)
    # ratio = 6/10 = 0.6, between partial_floor 0.5 and hit_floor 0.9
    result = experiment_verdict.apply_verdict(tmp_path, record["id"], actual=6.0)
    assert result["verdict"] == "partial"


def test_verdict_deterministic_same_inputs_twice(tmp_path):
    first = experiment_verdict.compute_verdict("higher", 10.0, 6.0, 0.9, 0.5)
    second = experiment_verdict.compute_verdict("higher", 10.0, 6.0, 0.9, 0.5)
    assert first == second == "partial"


def test_verdict_written_back_to_file(tmp_path):
    _seed_graph(tmp_path)
    record = _author(tmp_path)
    experiment_verdict.apply_verdict(tmp_path, record["id"], actual=10.0)
    fm, _body = experiment_spec.read_experiment(tmp_path, record["id"])
    assert fm["status"] == "concluded"
    assert fm["verdict"] == "hit"
    assert fm["actual"] == 10.0


# ---------------------------------------------------------------------------
# A negative target sign-flips compute_verdict's ratio (direction="higher",
# target=-5, actual=-10 -> ratio=2.0 >= hit_floor -> "hit", even though -10
# is a WORSE outcome than -5). validate_decision_rule must reject a
# non-positive target upfront (not just target == 0), converting the
# silent-wrong-verdict into a clear authoring-time error.
# ---------------------------------------------------------------------------

def test_validate_decision_rule_rejects_negative_target():
    rule = dict(_DECISION_RULE)
    rule["target"] = -5.0
    with pytest.raises(experiment_spec.ExperimentError):
        experiment_spec.validate_decision_rule(rule)


def test_validate_decision_rule_rejects_zero_target():
    rule = dict(_DECISION_RULE)
    rule["target"] = 0.0
    with pytest.raises(experiment_spec.ExperimentError):
        experiment_spec.validate_decision_rule(rule)


def test_validate_decision_rule_accepts_positive_target():
    experiment_spec.validate_decision_rule(dict(_DECISION_RULE))  # must not raise


def test_validate_decision_rule_rejects_nan_and_inf_target():
    # A NaN target slips a bare `target <= 0` check (every NaN comparison is
    # False), then makes every ratio NaN -> a constant "miss" regardless of the
    # actual result. Must be rejected upfront like a non-positive target.
    for bad in (float("nan"), float("inf"), float("-inf")):
        rule = dict(_DECISION_RULE)
        rule["target"] = bad
        with pytest.raises(experiment_spec.ExperimentError):
            experiment_spec.validate_decision_rule(rule)


def test_verdict_lower_with_negative_actual_is_hit_not_miss():
    # "lower is better" with a negative actual (an ordinary delta-metric value:
    # churn delta -3% means churn fell) is at least as good as actual == 0 and
    # must never verdict worse than it -- `target/actual` would otherwise
    # sign-flip and cliff an excellent result straight to "miss".
    for actual in (-1000.0, -1.0, 0.0):
        assert experiment_verdict.compute_verdict(
            "lower", target=5.0, actual=actual, hit_floor=0.9, partial_floor=0.5
        ) == "hit"
    # a positive actual well above target still resolves normally (regression
    # guard: ratio = 5/100 = 0.05, below both floors -> miss)
    assert experiment_verdict.compute_verdict(
        "lower", target=5.0, actual=100.0, hit_floor=0.9, partial_floor=0.5
    ) == "miss"


def test_author_rejects_negative_target(tmp_path):
    _seed_graph(tmp_path)
    with pytest.raises(experiment_spec.ExperimentError):
        _author(tmp_path, decision_rule={
            "direction": "higher", "target": -5.0, "hit_floor": 0.9, "partial_floor": 0.5,
        })


# ---------------------------------------------------------------------------
# Concurrency: apply_verdict()'s read-modify-write runs under the same
# .experiments.lock flock experiment_spec.author() already takes.
# ---------------------------------------------------------------------------

def test_apply_verdict_acquires_lock_on_experiments_lock_file(tmp_path, monkeypatch):
    _seed_graph(tmp_path)
    record = _author(tmp_path)

    calls = []
    real_flock = experiment_verdict.fcntl.flock

    def _spy_flock(fd, op):
        calls.append(op)
        return real_flock(fd, op)

    monkeypatch.setattr(experiment_verdict.fcntl, "flock", _spy_flock)

    experiment_verdict.apply_verdict(tmp_path, record["id"], actual=10.0)

    assert experiment_verdict.fcntl.LOCK_EX in calls
    assert experiment_verdict.fcntl.LOCK_UN in calls
    lock_path = tmp_path / "docs" / "product" / "shape" / "experiments" / ".experiments.lock"
    assert lock_path.is_file()


# ---------------------------------------------------------------------------
# Malformed verdict input -> clear error, not a crash
# ---------------------------------------------------------------------------

def test_verdict_unknown_experiment_raises_clear_error(tmp_path):
    _seed_graph(tmp_path)
    with pytest.raises(experiment_verdict.VerdictError):
        experiment_verdict.apply_verdict(tmp_path, "EXP-999", actual=10.0)


def test_verdict_non_numeric_actual_raises_clear_error(tmp_path):
    _seed_graph(tmp_path)
    record = _author(tmp_path)
    with pytest.raises(experiment_verdict.VerdictError):
        experiment_verdict.apply_verdict(tmp_path, record["id"], actual="not-a-number")


def test_verdict_missing_decision_rule_raises_clear_error(tmp_path):
    _seed_graph(tmp_path)
    record = _author(tmp_path)
    target = tmp_path / "docs" / "product" / "shape" / "experiments" / (record["id"] + ".md")
    # Corrupt the file: drop decision_rule entirely (a hand-edited/malformed file).
    text = target.read_text(encoding="utf-8")
    text = text.replace("decision_rule:", "decision_rule_typo:")
    target.write_text(text, encoding="utf-8")
    with pytest.raises(experiment_verdict.VerdictError):
        experiment_verdict.apply_verdict(tmp_path, record["id"], actual=10.0)


# ---------------------------------------------------------------------------
# EXP monotonic (explicit EXP-1/EXP-2 exist -> alloc EXP-3)
# ---------------------------------------------------------------------------

def test_exp_monotonic_alloc_after_existing_ids(tmp_path):
    _seed_graph(tmp_path)
    exp_dir = tmp_path / "docs" / "product" / "shape" / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "EXP-1.md").write_text("---\nid: EXP-1\n---\n", encoding="utf-8")
    (exp_dir / "EXP-2.md").write_text("---\nid: EXP-2\n---\n", encoding="utf-8")
    record = _author(tmp_path)
    assert record["id"] == "EXP-3"


# ---------------------------------------------------------------------------
# Containment: WRITE only under docs/product/shape/experiments/
# ---------------------------------------------------------------------------

def test_containment_escape_raises(tmp_path):
    with pytest.raises(PermissionError):
        experiment_spec.experiment_path(tmp_path, "../stories/x.md")


def test_containment_escape_via_dotdot_in_id_raises(tmp_path):
    with pytest.raises(PermissionError):
        experiment_spec.experiment_path(tmp_path, "../../etc/passwd")


def test_containment_escape_raises_within_experimenterror_family(tmp_path):
    """experiment_path's containment escape must be catchable by an
    `except ExperimentError` -- the same family main()'s `--add`/`--list`
    dispatch already catches -- not only PermissionError. Before the fix,
    experiment_path raised a bare PermissionError with no relation to
    ExperimentError, so an escape reaching main() would surface as a raw
    traceback instead of a clean CLI error."""
    with pytest.raises(experiment_spec.ExperimentError):
        experiment_spec.experiment_path(tmp_path, "../stories/x.md")


# ---------------------------------------------------------------------------
# Fail-open on a malformed frontmatter YAML: PyYAML's timestamp constructor
# raises a bare ValueError (not yaml.YAMLError) on an out-of-range unquoted
# date; a non-UTF-8 file raises UnicodeDecodeError. Both must surface as a
# clear ExperimentError, never a raw parser traceback.
# ---------------------------------------------------------------------------

def _experiments_dir(proj: Path) -> Path:
    d = proj / "docs" / "product" / "shape" / "experiments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_read_experiment_bad_date_frontmatter_fails_open_not_crash(tmp_path):
    proj = tmp_path
    (_experiments_dir(proj) / "EXP-1.md").write_text(
        "---\n"
        "id: EXP-1\n"
        "hypothesis: h\n"
        "linked_to: []\n"
        "design: {}\n"
        "success_metric: m\n"
        "decision_rule: {direction: higher, target: 1.0, hit_floor: 0.9, partial_floor: 0.5}\n"
        "status: draft\n"
        "ts: 2026-13-99\n"
        "---\n\n# EXP-1\n",
        encoding="utf-8",
    )
    with pytest.raises(experiment_spec.ExperimentError):
        experiment_spec.read_experiment(proj, "EXP-1")


def test_read_experiment_yaml_timestamp_tag_frontmatter_fails_open_not_crash(tmp_path):
    """A `!!timestamp` explicit-tag value raises a bare AttributeError from
    PyYAML's construct_yaml_timestamp -- not yaml.YAMLError or ValueError --
    and must still fail open, never a raw parser traceback."""
    proj = tmp_path
    (_experiments_dir(proj) / "EXP-1.md").write_text(
        "---\n"
        "id: EXP-1\n"
        "hypothesis: h\n"
        "linked_to: []\n"
        "design: {}\n"
        "success_metric: m\n"
        "decision_rule: {direction: higher, target: 1.0, hit_floor: 0.9, partial_floor: 0.5}\n"
        "status: draft\n"
        "ts: !!timestamp 'not a ts'\n"
        "---\n\n# EXP-1\n",
        encoding="utf-8",
    )
    with pytest.raises(experiment_spec.ExperimentError):
        experiment_spec.read_experiment(proj, "EXP-1")


def test_read_experiment_non_utf8_file_fails_open_not_crash(tmp_path):
    proj = tmp_path
    (_experiments_dir(proj) / "EXP-1.md").write_bytes(
        b"---\nid: EXP-1\nhypothesis: \xff\xfe bad\n---\n\nbody\n"
    )
    with pytest.raises(experiment_spec.ExperimentError):
        experiment_spec.read_experiment(proj, "EXP-1")


# ---------------------------------------------------------------------------
# list_experiments must never surface a raw traceback over one malformed
# hand-edited record -- skip it and keep listing the rest.
# ---------------------------------------------------------------------------

def test_list_experiments_skips_malformed_record_not_crash(tmp_path):
    _seed_graph(tmp_path)
    good = _author(tmp_path)
    (_experiments_dir(tmp_path) / "EXP-2.md").write_text(
        "---\nid: EXP-2\nts: 2026-13-99\n---\n\nbad\n", encoding="utf-8",
    )
    listed = experiment_spec.list_experiments(tmp_path)
    assert [e["id"] for e in listed] == [good["id"]]


# ---------------------------------------------------------------------------
# Drift guards: the Python-side enums must not silently diverge from their
# JSON schema counterparts.
# ---------------------------------------------------------------------------

def test_experiment_verdicts_match_schema_enum():
    import json

    schema_path = _SHAPE_SCRIPTS.parent / "schemas" / "experiment.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_enum = [v for v in schema["properties"]["verdict"]["enum"] if v is not None]
    assert set(experiment_verdict.VERDICTS) == set(schema_enum)


def test_experiment_schema_status_enum_has_no_dead_running_value():
    """experiment_spec.author() only ever writes 'draft' and
    experiment_verdict.apply_verdict() only ever writes 'concluded' -- a
    third 'running' enum value that no writer ever emits is dead schema
    surface that can quietly drift from reality."""
    import json

    schema_path = _SHAPE_SCRIPTS.parent / "schemas" / "experiment.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["status"]["enum"] == ["draft", "concluded"]


def test_experiment_schema_documents_verdict_actor():
    """apply_verdict() writes `verdict_actor` onto the record whenever an actor
    is supplied (experiment_verdict.py). The schema must DOCUMENT that field --
    every other field a writer emits is a named property here, so leaning on
    additionalProperties to smuggle verdict_actor in is an under-documented
    surface, not a design choice."""
    import json

    schema_path = _SHAPE_SCRIPTS.parent / "schemas" / "experiment.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "verdict_actor" in schema["properties"]


# ---------------------------------------------------------------------------
# Verification-tiering rule hard guards: no running code, no orchestrator import
# ---------------------------------------------------------------------------

_SCRIPT_NAMES = ("experiment_spec.py", "experiment_verdict.py")
_FORBIDDEN_RUNNING_TOKENS = ("subprocess", "urllib", "requests", "http.client", "socket.")


def test_no_running_code_in_scripts():
    for name in _SCRIPT_NAMES:
        src = (_SHAPE_SCRIPTS / name).read_text(encoding="utf-8")
        for token in _FORBIDDEN_RUNNING_TOKENS:
            assert token not in src, f"{name} contains forbidden running-code token: {token}"


def test_no_orchestrator_import():
    for name in _SCRIPT_NAMES:
        src = (_SHAPE_SCRIPTS / name).read_text(encoding="utf-8")
        assert "import orchestrator" not in src
        assert "from orchestrator" not in src


def test_list_experiments_reads_zero_padded_filename(tmp_path):
    """A hand-authored zero-padded `EXP-01.md` still matches the file regex, so
    `--list` must surface it -- re-deriving a canonical `EXP-<num>.md` path and
    looking THAT up silently drops the file that is actually on disk (mirrors
    task_model.list_tasks, which reads the glob-matched path)."""
    _seed_graph(tmp_path)
    record = _author(tmp_path)  # writes EXP-1.md
    exp_dir = tmp_path / "docs" / "product" / "shape" / "experiments"
    (exp_dir / "EXP-1.md").rename(exp_dir / "EXP-01.md")

    listed = experiment_spec.list_experiments(tmp_path)

    assert len(listed) == 1, "zero-padded EXP-01.md dropped from --list"
    assert listed[0]["id"] == record["id"]
