"""test_cook_parallel_plan.py — opt-in parallel cook partitioner (PX).

The partitioner is the safety core of `cook --parallel`: it decides which phases
may run concurrently (parallel_safe AND disjoint file ownership) and which must
stay sequential. It NEVER lets two phases that touch overlapping paths run in the
same concurrent batch — overlap demotes both to sequential and is reported, so the
fallback is logged, never silent. Default resolution is OFF (today's behavior).
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cook_parallel_plan as cpp  # noqa: E402


# ---- opt-in resolution (default OFF = non-breaking) -------------------------

def test_default_off_when_nothing_set():
    assert cpp.is_parallel_enabled(flag=None, env=None, config=None) is False


def test_flag_wins_over_everything():
    assert cpp.is_parallel_enabled(flag=True, env="0", config=False) is True
    assert cpp.is_parallel_enabled(flag=False, env="1", config=True) is False


def test_env_over_config():
    assert cpp.is_parallel_enabled(flag=None, env="1", config=False) is True
    assert cpp.is_parallel_enabled(flag=None, env="0", config=True) is False


def test_config_when_only_config():
    assert cpp.is_parallel_enabled(flag=None, env=None, config=True) is True


# ---- partition: the disjoint-ownership grouping ----------------------------

def test_all_disjoint_parallel_safe_run_concurrently():
    phases = [
        {"id": "P4", "parallel_safe": True, "owns": ["harness/hooks/a.py"]},
        {"id": "P6", "parallel_safe": True, "owns": ["harness/plugins/x/SKILL.md"]},
    ]
    out = cpp.partition(phases)
    assert set(out["parallel"]) == {"P4", "P6"}
    assert out["sequential"] == []
    assert out["conflicts"] == []


def test_non_parallel_safe_phase_is_sequential():
    phases = [
        {"id": "P2", "parallel_safe": False, "owns": ["harness/scripts/v.py"]},
        {"id": "P4", "parallel_safe": True, "owns": ["harness/hooks/a.py"]},
    ]
    out = cpp.partition(phases)
    assert out["parallel"] == ["P4"]
    assert out["sequential"] == ["P2"]


def test_overlapping_ownership_demotes_both_to_sequential():
    phases = [
        {"id": "PA", "parallel_safe": True, "owns": ["harness/scripts/shared.py"]},
        {"id": "PB", "parallel_safe": True, "owns": ["harness/scripts/shared.py", "x"]},
        {"id": "PC", "parallel_safe": True, "owns": ["harness/scripts/lonely.py"]},
    ]
    out = cpp.partition(phases)
    # PC is disjoint -> parallel; PA/PB share a path -> both sequential + reported
    assert out["parallel"] == ["PC"]
    assert set(out["sequential"]) == {"PA", "PB"}
    assert any(set([c["a"], c["b"]]) == {"PA", "PB"}
               and "harness/scripts/shared.py" in c["shared"]
               for c in out["conflicts"])


def test_overlapping_globs_demote_to_sequential():
    # two parallel_safe phases whose glob SCOPES overlap (superset glob) must NOT run
    # concurrently into the same files — even before the files materialize.
    phases = [
        {"id": "PA", "parallel_safe": True, "owns": ["src/api/*.py"]},
        {"id": "PB", "parallel_safe": True, "owns": ["src/**/*.py"]},
    ]
    out = cpp.partition(phases)
    assert out["parallel"] == [] and len(out["conflicts"]) == 1


def test_path_inside_a_glob_scope_conflicts():
    phases = [
        {"id": "PA", "parallel_safe": True, "owns": ["src/api/*.py"]},
        {"id": "PB", "parallel_safe": True, "owns": ["src/api/handler.py"]},
    ]
    assert cpp.partition(phases)["parallel"] == []


def test_disjoint_glob_dirs_stay_parallel():
    phases = [
        {"id": "PA", "parallel_safe": True, "owns": ["src/api/*.py"]},
        {"id": "PB", "parallel_safe": True, "owns": ["src/web/*.py"]},
    ]
    assert sorted(cpp.partition(phases)["parallel"]) == ["PA", "PB"]


def test_path_spelling_variants_conflict():
    # './src/x.py' and 'src/x.py' name the same to-be-created file -> must conflict.
    phases = [
        {"id": "PA", "parallel_safe": True, "owns": ["src/new.py"]},
        {"id": "PB", "parallel_safe": True, "owns": ["./src/new.py"]},
    ]
    assert cpp.partition(phases)["parallel"] == []


def test_sequential_order_preserves_input_order():
    phases = [
        {"id": "P1", "parallel_safe": False, "owns": []},
        {"id": "P2", "parallel_safe": False, "owns": []},
    ]
    out = cpp.partition(phases)
    assert out["sequential"] == ["P1", "P2"]


def test_empty_input():
    out = cpp.partition([])
    assert out == {"parallel": [], "sequential": [], "conflicts": []}


# ---- glob expansion at the edge (real-tree ownership) ----------------------

def test_expand_owns_globs_against_tree(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "other.txt").write_text("", encoding="utf-8")
    got = cpp.expand_owns(["pkg/*.py"], root=tmp_path)
    assert got == {"pkg/a.py", "pkg/b.py"}


def test_expand_owns_literal_passthrough_when_no_match(tmp_path):
    # a literal path with no glob char and no file yet still counts as owned
    got = cpp.expand_owns(["harness/hooks/new_hook.py"], root=tmp_path)
    assert got == {"harness/hooks/new_hook.py"}
