"""Tests for review_recall.py — the deterministic half of hs:code-review recall-mode.

Recall-mode scales the Stage-2 finding production by an effort level
(low|medium|high|xhigh|max). This module owns the reproducible decisions:
effort resolution, the effort→breadth lookup, the git diff-source fallback, and a
deterministic scope assessment. The LLM judgment (fan-out, verify, sweep) layers on
top in references/recall-mode.md — never here.

The recall engine never touches the gate: verdict-truth-table + dismissals +
review-decision.json are unchanged. These tests pin only the resolvers.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import review_recall as rr  # noqa: E402
from conftest import _git  # noqa: E402

LEVELS = ["low", "medium", "high", "xhigh", "max"]


def _run_recall(args, cwd=None):
    cmd = [sys.executable, str(_SCRIPTS / "review_recall.py"), *args]
    if cwd is None:
        cwd = str(_ROOT)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


# --- effort resolution --------------------------------------------------------

def test_resolve_effort_default_is_low():
    # No flag / env / config override → today's single-pass behavior.
    assert rr.resolve_effort(flag=None, env=None, config=None) == "low"


def test_resolve_effort_flag_wins():
    assert rr.resolve_effort(flag="max", env="medium", config="high") == "max"


def test_resolve_effort_env_over_config():
    assert rr.resolve_effort(flag=None, env="high", config="medium") == "high"


def test_resolve_effort_config_over_default():
    assert rr.resolve_effort(flag=None, env=None, config="xhigh") == "xhigh"


def test_resolve_effort_invalid_falls_to_low():
    assert rr.resolve_effort(flag="ultra", env=None, config=None) == "low"
    assert rr.resolve_effort(flag=None, env="garbage", config=None) == "low"


def test_resolve_effort_case_insensitive():
    assert rr.resolve_effort(flag="HIGH", env=None, config=None) == "high"


# --- breadth lookup -----------------------------------------------------------

def test_breadth_every_level_present():
    for lv in LEVELS:
        b = rr.breadth_for(lv)
        assert {"fan_out", "lenses", "verify", "sweep"} <= set(b)


def test_breadth_low_is_single_pass():
    b = rr.breadth_for("low")
    assert b["fan_out"] is False
    assert b["lenses"] == 1
    assert b["sweep"] is False


def test_breadth_lenses_monotonic_non_decreasing():
    counts = [rr.breadth_for(lv)["lenses"] for lv in LEVELS]
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]  # low strictly below max


def test_breadth_sweep_off_low_medium_on_high_plus():
    assert rr.breadth_for("low")["sweep"] is False
    assert rr.breadth_for("medium")["sweep"] is False
    for lv in ("high", "xhigh", "max"):
        assert rr.breadth_for(lv)["sweep"] is True


def test_breadth_fan_out_off_only_at_low():
    assert rr.breadth_for("low")["fan_out"] is False
    for lv in ("medium", "high", "xhigh", "max"):
        assert rr.breadth_for(lv)["fan_out"] is True


# --- config load --------------------------------------------------------------

def test_config_parses_with_five_levels_and_valid_default():
    cfg = rr.load_config()
    assert set(cfg["levels"]) == set(LEVELS)
    assert cfg["default"] in LEVELS
    assert cfg["default"] == "low"


def test_load_config_malformed_yaml_falls_back_to_defaults(tmp_path):
    # yaml.YAMLError is NOT a ValueError — a malformed config must still fall back to
    # the built-in defaults (the loader is advisory, never a hard-fail).
    bad = tmp_path / "bad.yaml"
    bad.write_text("default: low\nlevels: [unclosed\n", encoding="utf-8")
    cfg = rr.load_config(str(bad))
    assert cfg["default"] == "low"
    assert set(cfg["levels"]) == set(LEVELS)


def test_load_config_missing_file_falls_back(tmp_path):
    cfg = rr.load_config(str(tmp_path / "nope.yaml"))
    assert set(cfg["levels"]) == set(LEVELS)


# --- diff-source resolution ---------------------------------------------------

def _init_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("1\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "init")


def test_diff_source_no_upstream_no_main_falls_to_head1(tmp_path):
    # A repo with no upstream and genuinely no main/master branch — rename the init
    # branch away so the final HEAD~1 fallback is exercised.
    r = tmp_path / "repo"
    _init_repo(r)
    _git(r, "branch", "-m", "trunk")
    _git(r, "checkout", "-q", "-b", "feature/x")
    res = rr.resolve_diff_source(target=None, root=str(r))
    assert res["range"] == "HEAD~1"


def test_diff_source_prefers_main_when_present(tmp_path):
    r = tmp_path / "repo"
    _init_repo(r)
    _git(r, "branch", "-M", "main")
    _git(r, "checkout", "-q", "-b", "feature/y")
    res = rr.resolve_diff_source(target=None, root=str(r))
    assert res["range"] == "main...HEAD"


def test_diff_source_prefers_upstream(tmp_path):
    # Clone gives the working branch a real upstream → @{u}...HEAD wins over main.
    origin = tmp_path / "origin"
    _init_repo(origin)
    _git(origin, "branch", "-M", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)],
                   check=True, capture_output=True, text=True)
    _git(clone, "config", "user.email", "t@t.t")
    _git(clone, "config", "user.name", "t")
    res = rr.resolve_diff_source(target=None, root=str(clone))
    assert res["range"] == "@{u}...HEAD"


def test_diff_source_explicit_target_revision_is_used(tmp_path):
    # A target naming a real revision (branch/tag/sha) → <target>...HEAD, overriding
    # the upstream/main fallback.
    r = tmp_path / "repo"
    _init_repo(r)
    _git(r, "branch", "base")
    (r / "a.txt").write_text("2\n", encoding="utf-8")
    _git(r, "commit", "-aqm", "second")
    res = rr.resolve_diff_source(target="base", root=str(r))
    assert res["range"] == "base...HEAD"


def test_diff_source_non_revision_target_falls_through(tmp_path):
    # A non-revision target (a mode token like --pending) does NOT resolve here; it
    # falls through to the chain rather than producing a bogus "--pending...HEAD".
    r = tmp_path / "repo"
    _init_repo(r)
    _git(r, "branch", "-m", "trunk")
    _git(r, "checkout", "-q", "-b", "feature/z")
    res = rr.resolve_diff_source(target="--pending", root=str(r))
    assert res["range"] == "HEAD~1"


def test_diff_source_resolves_main_via_origin_head(tmp_path):
    # A clone whose working branch has NO upstream must resolve the integration
    # branch through refs/remotes/origin/HEAD — the realistic production path.
    origin = tmp_path / "origin"
    _init_repo(origin)
    _git(origin, "branch", "-M", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)],
                   check=True, capture_output=True, text=True)
    _git(clone, "config", "user.email", "t@t.t")
    _git(clone, "config", "user.name", "t")
    # A brand-new local branch has no @{u} → _has_upstream False → _main_branch must
    # fall to origin/HEAD (no local 'main' is checked out in a fresh feature branch).
    _git(clone, "checkout", "-q", "-b", "feature/local")
    assert rr._has_upstream(str(clone)) is False
    assert rr._main_branch(str(clone)) == "main"
    res = rr.resolve_diff_source(target=None, root=str(clone))
    assert res["range"] == "main...HEAD"


def test_diff_source_clean_tree_excludes_worktree(tmp_path):
    r = tmp_path / "repo"
    _init_repo(r)
    res = rr.resolve_diff_source(target=None, root=str(r))
    assert res["include_worktree"] is False


def test_diff_source_dirty_tree_includes_worktree(tmp_path):
    r = tmp_path / "repo"
    _init_repo(r)
    (r / "a.txt").write_text("changed\n", encoding="utf-8")
    res = rr.resolve_diff_source(target=None, root=str(r))
    assert res["include_worktree"] is True


# --- scope assessment ---------------------------------------------------------

def test_assess_scope_trivial_diff_is_small_low(tmp_path):
    res = rr.assess_scope(["README.md"], root=str(tmp_path))
    assert res["scope"] == "small"
    assert res["suggested_effort"] == "low"


def test_assess_scope_auth_signal_bumps_suggestion(tmp_path):
    res = rr.assess_scope(["src/auth/login.py"], root=str(tmp_path))
    assert "auth" in res["signals"]
    # Pin the exact mapping the code produces — a loose set would not catch an
    # over-escalation to xhigh/max nor a downgrade as cleanly.
    assert res["suggested_effort"] == "high"


def test_assess_scope_large_filecount_bumps_suggestion(tmp_path):
    files = ["src/mod_%d.py" % i for i in range(12)]
    res = rr.assess_scope(files, root=str(tmp_path))
    assert res["scope"] == "large"
    assert res["suggested_effort"] == "high"


def test_assess_scope_broken_rubric_is_loud_not_silent(tmp_path, monkeypatch):
    # A PRESENT-but-broken risk rubric must NOT silently drop signals (which would
    # downgrade the suggestion on risky diffs); it surfaces as an error.
    import risk_rubric
    monkeypatch.setenv("HARNESS_RISK_RUBRIC", str(tmp_path / "missing-rubric.yaml"))
    with pytest.raises(risk_rubric.RiskRubricError):
        rr.assess_scope(["src/auth/login.py"], root=str(tmp_path))


def test_assess_scope_is_deterministic(tmp_path):
    files = ["src/auth/login.py", "src/util.py"]
    a = rr.assess_scope(files, root=str(tmp_path))
    b = rr.assess_scope(files, root=str(tmp_path))
    assert a == b


def test_assess_scope_moderate_filecount_is_medium(tmp_path):
    files = ["src/mod_%d.py" % i for i in range(4)]
    res = rr.assess_scope(files, root=str(tmp_path))
    assert res["scope"] == "moderate"
    assert res["suggested_effort"] == "medium"


# --- prose contracts (dev-repo only: assert the shipped guidance, not behavior) ---

_SKILLS = _ROOT / "harness" / "plugins" / "hs" / "skills"


@pytest.mark.dev_repo
def test_code_review_skill_advertises_effort_and_flags():
    body = (_SKILLS / "code-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "low|medium|high|xhigh|max" in body
    assert "--auto" in body
    assert "--fix-auto" in body
    assert "--in-place" in body


@pytest.mark.dev_repo
def test_recall_mode_reference_complete():
    txt = (_SKILLS / "code-review" / "references" / "recall-mode.md").read_text(encoding="utf-8")
    low = txt.lower()
    for needle in ("fan-out", "verify", "sweep", "gate", "unchanged",
                   "askuserquestion", "scope", "--auto", "fallback"):
        assert needle in low, needle
    # high+ orchestration via the ultracode Workflow tool
    assert "workflow" in low and "ultracode" in low


@pytest.mark.dev_repo
def test_recall_mode_documents_base_workflow_and_four_stamp_labels():
    # high+ prefers the shared named base over a hand-written inline script, and the
    # report stamps which of the four tiers ran. Plugin workflows resolve under the
    # `hs:` namespace (the bare name does not), so the doc must name the prefixed form.
    txt = (_SKILLS / "code-review" / "references" / "recall-mode.md").read_text(encoding="utf-8")
    assert "hs:base-pipeline-verify" in txt
    for stamp in ("Workflow(name)", "Workflow(scriptPath)", "Workflow(inline)",
                  "inline-Task fallback"):
        assert stamp in txt, stamp


@pytest.mark.dev_repo
def test_review_dimensions_has_named_footguns():
    txt = (_SKILLS / "code-review" / "references" / "review-dimensions.md").read_text(
        encoding="utf-8").lower()
    for needle in ("removed-behavior", "wrapper/proxy", "language-pitfall", "cross-file"):
        assert needle in txt, needle


@pytest.mark.dev_repo
def test_fix_skill_has_auto_flag():
    body = (_SKILLS / "fix" / "SKILL.md").read_text(encoding="utf-8")
    assert "--auto" in body


@pytest.mark.dev_repo
def test_recall_not_gate_driven():
    # Decision 4: no new stage-policy requires entry — recall changes finding
    # production, never the gate. Pin that review_recall is absent from stage requires.
    pol = (_ROOT / "harness" / "data" / "stage-policy.yaml").read_text(encoding="utf-8")
    # The real invariant: the recall script is not wired as a stage requirement. A
    # bare "recall" substring check would trip on any future comment mentioning
    # "recall-mode" — narrow it to the script name.
    assert "review_recall" not in pol


# --- multi-round profile helpers (P2) -----------------------------------------

CAPS = {"max_rounds": 5, "max_lenses_per_round": 8}


def test_round_budget_caps_rounds_and_lenses():
    out = rr.round_budget(rounds=9, lenses=20, caps=CAPS)
    assert out == {"rounds": 5, "lenses": 8, "capped": True}


def test_round_budget_under_cap_not_capped():
    out = rr.round_budget(rounds=2, lenses=3, caps=CAPS)
    assert out == {"rounds": 2, "lenses": 3, "capped": False}


def test_round_budget_defaults_caps_when_absent():
    # caps omitted → built-in ceiling (max_rounds=5, max_lenses_per_round=8)
    out = rr.round_budget(rounds=9, lenses=9, caps=None)
    assert out["rounds"] == 5 and out["lenses"] == 8 and out["capped"] is True


def test_rounds_override_clamped_by_caps():
    # a --rounds 9 per-run override must clamp to caps.max_rounds
    assert rr.round_budget(rounds=9, lenses=1, caps=CAPS)["rounds"] == 5


def test_blind_payload_excludes_main_findings():
    payload = rr.blind_payload(scope="diff", artifact_path="plans/x/artifacts/review-decision.yaml")
    assert payload["scope"] == "diff"
    assert payload["artifact_path"] == "plans/x/artifacts/review-decision.yaml"
    assert "findings" not in payload
    assert "main_findings" not in payload


def test_resolve_profile_breadth_maps_effort():
    profile = {"effort": "high", "rounds": 3, "compounding": True,
               "per_aspect": True, "blind_main_sub": False, "refute": True,
               "aspects": ["security", "dry"], "scope": "diff"}
    out = rr.resolve_profile_breadth(profile)
    assert out["lenses"] == 5          # code-review.yaml high → 5 lenses
    assert out["rounds"] == 3          # carried from the profile
    assert out["compounding"] is True and out["refute"] is True
    assert out["per_aspect"] is True and out["blind_main_sub"] is False
    assert out["aspects"] == ["security", "dry"]
    assert out["sweep"] is True        # high sweeps


def test_resolve_profile_breadth_defaults_rounds_floor():
    # a profile with rounds<1 (or missing) floors to 1 round
    out = rr.resolve_profile_breadth({"effort": "low", "rounds": 0})
    assert out["rounds"] == 1
    assert out["lenses"] == 1          # low → single lens


# --- in-place flag -----------------------------------------------------------

def test_main_no_in_place_flag_defaults_false(tmp_path):
    # default: review must spawn agents; in_place is false unless user opts in.
    out = _run_recall(["--scope", "a.py"], cwd=str(tmp_path))
    assert out["in_place"] is False


def test_main_in_place_flag_is_true():
    out = _run_recall(["--in-place", "--scope", "a.py"])
    assert out["in_place"] is True


def test_main_in_place_alias_inline():
    out = _run_recall(["--inline", "--scope", "a.py"])
    assert out["in_place"] is True


def test_main_in_place_does_not_change_effort_or_breadth():
    out = _run_recall(["--in-place", "--effort", "high", "--scope", "a.py"])
    assert out["in_place"] is True
    assert out["effort"] == "high"
    assert out["breadth"]["lenses"] == 5
