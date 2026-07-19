"""Tests for the decision_capture detector (the A-leg of memory-v2).

The detector answers ONE deterministic question: did this session ship a
*decision-shaped* change — a NEW hook/script/rule/agent/skill module, or an edit
to a gate-config file — without a matching record in the decision ledger
(docs/decisions.md) or a plan Validation Log (plans/**/plan.md)?

The judgment is the pure `assess(changes)` — fully unit-testable without a repo.
The git read is isolated in `_porcelain_changes`/`collect`, smoked once over a
real temp git repo so the porcelain parse + status mapping are covered.
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import decision_capture as dc  # noqa: E402
from conftest import _git  # noqa: E402


# ---------------------------------------------------------------------------
# assess() — pure judgment, no git
# ---------------------------------------------------------------------------

def test_new_hook_without_record_fires():
    sig = dc.assess([("A", "harness/hooks/new_thing.py")])
    assert sig is not None
    assert sig["type"] == dc.SIGNAL_TYPE
    assert sig["subjects"] == ["harness/hooks/new_thing.py"]


def test_new_script_with_decisions_record_is_clean():
    sig = dc.assess([
        ("A", "harness/scripts/new_detector.py"),
        ("M", "docs/decisions.md"),
    ])
    assert sig is None  # the ledger moved → recorded


def test_new_module_with_plan_validation_log_is_clean():
    # a plan.md edit (where DECs are drafted pre-register) counts as recording
    sig = dc.assess([
        ("A", "harness/rules/new-rule.md"),
        ("M", "plans/260618-1221-x/plan.md"),
    ])
    assert sig is None


def test_modified_existing_script_does_not_fire():
    # editing an existing module is NOT decision-shaped (only NEW modules are) —
    # this is the noise-control that keeps the nudge useful
    assert dc.assess([("M", "harness/scripts/existing.py")]) is None


def test_gate_config_edit_fires_even_when_modified():
    # a posture change to a gate-config file IS a decision, at any status
    sig = dc.assess([("M", "harness/hooks/stage-policy.yaml")])
    assert sig is not None
    assert "stage-policy.yaml" in sig["subjects"][0]


def test_new_skill_dir_fires():
    sig = dc.assess([("??", "harness/plugins/hs/skills/remember/SKILL.md")])
    assert sig is not None
    assert sig["subjects"][0].endswith("/SKILL.md")


def test_new_agent_fires():
    sig = dc.assess([("A", "harness/plugins/hs/agents/new-role.md")])
    assert sig is not None


def test_non_decision_change_is_clean():
    # a README / docs prose edit is not decision-shaped
    assert dc.assess([("M", "README.md"), ("M", "docs/codebase-summary.md")]) is None


def test_untracked_new_module_fires():
    # '??' (untracked) is a new file too, same as staged 'A'
    assert dc.assess([("??", "harness/scripts/fresh.py")]) is not None


def test_subjects_sorted_deduped_and_capped():
    changes = [("A", "harness/scripts/z%02d.py" % i) for i in range(12)]
    changes += [("A", "harness/scripts/z00.py")]  # duplicate
    sig = dc.assess(changes)
    assert sig is not None
    assert sig["subjects"] == sorted(sig["subjects"])          # sorted
    assert len(sig["subjects"]) == dc._SUBJECT_CAP             # capped
    assert sig["total"] == 12                                  # de-duped total


def test_record_alone_is_clean():
    # a session that only touched the ledger / a plan has nothing to nudge about
    assert dc.assess([("M", "docs/decisions.md")]) is None
    assert dc.assess([("M", "plans/p/plan.md")]) is None


def test_backslash_paths_normalized():
    sig = dc.assess([("A", "harness\\hooks\\win.py")])
    assert sig is not None
    assert sig["subjects"][0] == "harness/hooks/win.py"


# ---------------------------------------------------------------------------
# collect() / _porcelain_changes() — one smoke over a real temp git repo
# ---------------------------------------------------------------------------

def test_collect_over_real_repo_flags_new_untracked_module(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    # a brand-new, uncommitted hook module — decision-shaped, no ledger move
    hooks = repo / "harness" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "probe_new.py").write_text("# probe\n", encoding="utf-8")

    sig = dc.collect(repo)
    assert sig is not None
    assert any(s.endswith("harness/hooks/probe_new.py") for s in sig["subjects"])


def test_collect_degrades_to_none_outside_git(tmp_path):
    # not a git work tree → cannot read change state → None, never a crash
    assert dc.collect(tmp_path) is None


def test_collect_clean_repo_is_none(tmp_path):
    repo = tmp_path / "clean"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    assert dc.collect(repo) is None
