"""test_artifact_check.py — artifact presence gate + active-plan resolution.

Policy is data-driven from harness/data/stage-policy.yaml (stage → hard,
requires, require_plan default true). Hard stage with no resolvable plan →
block with a reason offering ALL THREE exits (create a plan / set
HARNESS_ACTIVE_PLAN / set require_plan: false). Artifacts live at
plans/<active>/artifacts/<kind>.json; validation is a minimal required-fields
check (no jsonschema dep). Hard-stage verdict policy: verification has no
FAILed check; review-decision verdict must be exactly PASS (PASS_WITH_RISK is
not enough to ship).

This is a PRESENCE gate: it proves the step ran, not who ran it.
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402


def _mk_plan(root: Path, name: str, status: str = "in_progress") -> Path:
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: %s\n---\n\n# %s\n" % (name, status, name),
        encoding="utf-8",
    )
    return d


def _verification(plan_dir: Path, *, verdict="PASS", checks=None, drop=None):
    rec = {
        "stage": "push", "plan": plan_dir.name, "actor": "user:alice",
        "ts": "2026-06-12T08:00:00+07:00",
        "checks": checks if checks is not None else [
            {"name": "pytest", "status": "PASS"}],
        "verdict": verdict,
    }
    for k in (drop or []):
        rec.pop(k)
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "verification.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def _review(plan_dir: Path, *, verdict="PASS", drop=None):
    rec = {"verdict": verdict, "reviewer": "user:bob", "role": "reviewer",
           "rationale": "looks correct"}
    for k in (drop or []):
        rec.pop(k)
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "review-decision.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def _critique(plan_dir: Path, *, verdict="PASS", drop=None):
    rec = {"verdict": verdict, "reviewer": "user:critique", "role": "critique",
           "rationale": "no blocker survived consolidation",
           "ts": "2026-06-12T08:00:00+07:00"}
    for k in (drop or []):
        rec.pop(k)
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "critique-consensus.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    monkeypatch.delenv("HARNESS_STAGE_POLICY", raising=False)
    return tmp_path


class TestResolveActivePlan:
    def test_env_override_wins(self, root, monkeypatch):
        d = _mk_plan(root, "260612-0800-feature-x")
        _mk_plan(root, "260612-0900-feature-y")  # newer, but env wins
        monkeypatch.setenv("HARNESS_ACTIVE_PLAN", str(d))
        assert ac.resolve_active_plan(root) == d

    def test_env_accepts_bare_dir_name_under_plans(self, root, monkeypatch):
        d = _mk_plan(root, "260612-0800-feature-x")
        monkeypatch.setenv("HARNESS_ACTIVE_PLAN", "260612-0800-feature-x")
        assert ac.resolve_active_plan(root) == d

    def test_single_in_progress_wins(self, root):
        # Exactly one in_progress plan among completed ones resolves cleanly.
        only = _mk_plan(root, "260612-0900-only")
        _mk_plan(root, "260611-0900-older", status="completed")
        _mk_plan(root, "260612-9999-done", status="completed")
        assert ac.resolve_active_plan(root) == only

    def test_multiple_in_progress_without_env_refused(self, root, capsys):
        # Two plans in_progress at once (e.g. concurrent sessions) is ambiguous:
        # the resolver must NOT silently guess one — it refuses (None) and names
        # the candidates so the operator disambiguates via HARNESS_ACTIVE_PLAN.
        _mk_plan(root, "260611-0900-older")
        _mk_plan(root, "260612-0900-newer")
        assert ac.resolve_active_plan(root) is None
        err = capsys.readouterr().err
        assert "260611-0900-older" in err and "260612-0900-newer" in err

    def test_multiple_in_progress_env_disambiguates(self, root, monkeypatch):
        # The env override resolves the ambiguity the bare resolver refuses.
        a = _mk_plan(root, "260611-0900-older")
        _mk_plan(root, "260612-0900-newer")
        monkeypatch.setenv("HARNESS_ACTIVE_PLAN", str(a))
        assert ac.resolve_active_plan(root) == a

    def test_no_plans_resolves_none(self, root):
        assert ac.resolve_active_plan(root) is None

    def test_plan_without_in_progress_status_skipped(self, root):
        _mk_plan(root, "260612-0800-done", status="completed")
        assert ac.resolve_active_plan(root) is None

    def test_hyphen_status_resolves_active(self, root):
        # Skills document `status: in-progress` (hyphen); it must resolve, else a
        # legit in-progress plan blocks every hard stage.
        d = _mk_plan(root, "260612-0800-hyphen", status="in-progress")
        assert ac.resolve_active_plan(root) == d

    def test_quoted_status_resolves_active(self, root):
        # `status: "in_progress"` is valid YAML and must resolve too.
        d = _mk_plan(root, "260612-0800-quoted", status='"in_progress"')
        assert ac.resolve_active_plan(root) == d

    def test_status_in_body_code_block_is_not_frontmatter(self, root):
        # A plan.md whose FRONTMATTER has no status but whose body quotes
        # `status: in_progress` (docs, example snippet) must not be picked up
        # as the active plan.
        d = root / "plans" / "260612-9999-doc-plan"
        d.mkdir(parents=True)
        (d / "plan.md").write_text(
            "---\ntitle: doc\n---\n\n# Doc\n\nExample:\n\n"
            "```\nstatus: in_progress\n```\n",
            encoding="utf-8",
        )
        assert ac.resolve_active_plan(root) is None

    def test_body_status_does_not_shadow_frontmatter_status(self, root):
        # Frontmatter says completed; a body line says in_progress. The
        # frontmatter value is the only one that counts.
        d = root / "plans" / "260612-9998-finished"
        d.mkdir(parents=True)
        (d / "plan.md").write_text(
            "---\ntitle: x\nstatus: completed\n---\n\n"
            "Earlier this plan was `status: in_progress`.\n"
            "status: in_progress\n",
            encoding="utf-8",
        )
        assert ac.resolve_active_plan(root) is None

    def test_plan_without_frontmatter_at_all_skipped(self, root):
        d = root / "plans" / "260612-9997-bare"
        d.mkdir(parents=True)
        (d / "plan.md").write_text(
            "# Bare\n\nstatus: in_progress\n", encoding="utf-8")
        assert ac.resolve_active_plan(root) is None


class TestCheckStageSoft:
    def test_soft_stage_passes_with_nothing(self, root):
        assert ac.check_stage("commit", root) is None

    def test_unknown_stage_passes(self, root):
        assert ac.check_stage("not-a-stage", root) is None


class TestCheckStageRequirePlan:
    def test_hard_stage_no_plan_blocks_with_three_exits(self, root):
        reason = ac.check_stage("push", root)
        assert reason is not None
        assert "HARNESS_ACTIVE_PLAN" in reason
        assert "require_plan" in reason
        assert "plan" in reason.lower()  # "create a plan" guidance

    def test_require_plan_false_skips_plan_requirement(self, root, monkeypatch):
        policy = root / "policy.yaml"
        policy.write_text(
            "stages:\n  push:\n    hard: true\n    require_plan: false\n"
            "    requires: []\n", encoding="utf-8")
        monkeypatch.setenv("HARNESS_STAGE_POLICY", str(policy))
        assert ac.check_stage("push", root) is None


class TestCheckStageCompletedFallback:
    """push may opt in (allow_completed_plan) to anchor a freshly-closed plan so
    a close-then-push clears instead of reading as no-active-plan."""

    def _policy(self, root, monkeypatch, *, allow):
        flag = "    allow_completed_plan: true\n" if allow else ""
        policy = root / "policy.yaml"
        policy.write_text(
            "stages:\n  push:\n    hard: true\n    requires: [verification]\n"
            + flag, encoding="utf-8")
        monkeypatch.setenv("HARNESS_STAGE_POLICY", str(policy))

    def test_completed_plan_clears_push_when_opted_in(self, root, monkeypatch):
        d = _mk_plan(root, "260612-0800-feature-x", status="completed")
        _verification(d)
        self._policy(root, monkeypatch, allow=True)
        assert ac.check_stage("push", root) is None

    def test_completed_plan_blocks_push_without_opt_in(self, root, monkeypatch):
        d = _mk_plan(root, "260612-0800-feature-x", status="completed")
        _verification(d)
        self._policy(root, monkeypatch, allow=False)
        reason = ac.check_stage("push", root)
        assert reason is not None and "active plan" in reason


class TestCheckStageArtifacts:
    def test_missing_verification_blocks_naming_artifact_and_path(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        reason = ac.check_stage("push", root)
        assert "verification" in reason
        assert str(d / "artifacts") in reason  # tells WHERE to create it

    def test_complete_verification_passes_push(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d)
        assert ac.check_stage("push", root) is None

    def test_missing_required_field_blocks_naming_field(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d, drop=["actor"])
        reason = ac.check_stage("push", root)
        assert reason is not None and "actor" in reason

    def test_malformed_json_blocks(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        a = d / "artifacts"
        a.mkdir()
        (a / "verification.json").write_text("{not json", encoding="utf-8")
        reason = ac.check_stage("push", root)
        assert reason is not None and "verification" in reason

    def test_failed_check_blocks_naming_the_check(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d, checks=[{"name": "pytest", "status": "FAIL"}])
        reason = ac.check_stage("push", root)
        assert reason is not None and "pytest" in reason

    def test_unknown_check_status_fails_closed(self, root):
        # per-check status is a closed enum {PASS,FAIL,SKIP}; anything else
        # (a crashed verifier writing ERROR/TIMEOUT, a typo) must fail CLOSED,
        # not slip through as "not FAIL → pass".
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d, checks=[{"name": "pytest", "status": "ERROR"}])
        reason = ac.check_stage("push", root)
        assert reason is not None and "pytest" in reason

    def test_missing_check_status_fails_closed(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d, checks=[{"name": "pytest"}])  # no status field
        assert ac.check_stage("push", root) is not None

    def test_skip_check_still_passes(self, root):
        # SKIP is a valid non-failure per the schema → must not block
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d, checks=[{"name": "pytest", "status": "PASS"},
                                 {"name": "lint", "status": "SKIP"}])
        assert ac.check_stage("push", root) is None

    def test_blocked_verification_verdict_blocks_even_with_passing_checks(self, root):
        # The verifier's own overall verdict is honored: BLOCKED stops the
        # stage even when every named check passed (the verdict can carry a
        # reason no single check expresses).
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d, verdict="BLOCKED",
                      checks=[{"name": "pytest", "status": "PASS"}])
        reason = ac.check_stage("push", root)
        assert reason is not None and "BLOCKED" in reason

    def test_pass_with_risk_verification_still_passes_push(self, root):
        # PASS_WITH_RISK on the VERIFICATION artifact is a conscious
        # soft-accept and does not block (review-decision is the artifact
        # that demands exactly PASS).
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d, verdict="PASS_WITH_RISK")
        assert ac.check_stage("push", root) is None

    def test_pr_requires_review_decision_too(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d)
        reason = ac.check_stage("pr", root)
        assert reason is not None and "review-decision" in reason

    def test_pr_with_pass_review_passes(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d)
        _review(d)
        _mk_team(root)
        _approve(d, root)
        _critique(d)
        assert ac.check_stage("pr", root) is None

    def test_pass_with_risk_review_is_not_enough_for_hard_stage(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d)
        _review(d, verdict="PASS_WITH_RISK")
        reason = ac.check_stage("pr", root)
        assert reason is not None
        assert "PASS_WITH_RISK" in reason  # names what it got vs what it needs

    def test_blocked_review_blocks(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d)
        _review(d, verdict="BLOCKED")
        assert ac.check_stage("pr", root) is not None

    def test_verification_from_another_plan_is_blocked(self, root):
        # cross-plan replay: a real PASS verification copied from another plan into
        # THIS plan's artifacts dir still names the other plan and must not clear
        # this gate (the writer records plan_dir.name).
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d)
        art = d / "artifacts" / "verification.json"
        rec = json.loads(art.read_text(encoding="utf-8"))
        rec["plan"] = "260612-0800-some-other-plan"
        art.write_text(json.dumps(rec), encoding="utf-8")
        reason = ac.check_stage("push", root)
        assert reason is not None and "plan" in reason.lower()

    def test_off_enum_verdict_fails_closed(self, root):
        # a crashed verifier that SKIPs every check but writes an off-enum verdict
        # (ERROR/TIMEOUT/empty) must fail CLOSED — the old BLOCKED-only denylist let
        # anything-but-"BLOCKED" through.
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d, verdict="ERROR",
                      checks=[{"name": "pytest", "status": "SKIP"}])
        reason = ac.check_stage("push", root)
        assert reason is not None and "verdict" in reason.lower()

    def test_plan_approval_from_another_plan_is_blocked(self, root):
        # identical plan bodies hash alike; an APPROVED artifact for another plan
        # replayed here still names that plan and must not clear this stage even
        # with a matching plan_hash.
        d = _mk_plan(root, "260612-0800-feature-x")
        _mk_team(root)
        _approve(d, root)
        art = d / "artifacts" / "plan-approval.json"
        rec = json.loads(art.read_text(encoding="utf-8"))
        rec["plan"] = "260612-0800-some-other-plan"
        art.write_text(json.dumps(rec), encoding="utf-8")
        reason = ac._check_artifact(d, "plan-approval", root=root)
        assert reason is not None and "plan" in reason.lower()


def _mk_team(root: Path, reviewers=("user:bob",), allow_self_review=False):
    d = root / "harness" / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "team.yaml").write_text(
        "reviewers: [%s]\nallow_self_review: %s\nclaims: {lease_s: 14400}\n"
        % (", ".join('"%s"' % r for r in reviewers),
           "true" if allow_self_review else "false"),
        encoding="utf-8")


def _approve(plan_dir: Path, root: Path, *, reviewer="user:bob",
             author="user:alice", verdict="APPROVED", stale_hash=False,
             drop=None):
    import plan_approval as pa
    rec = {
        "schema": "plan-approval/v1", "plan": plan_dir.name,
        "plan_hash": "0" * 12 if stale_hash else pa.plan_hash(plan_dir),
        "author": author, "reviewer": reviewer, "verdict": verdict,
        "rationale": "reviewed", "ts": "2026-06-12T08:00:00+07:00",
    }
    if not stale_hash:
        rec["file_hashes"] = pa.file_hashes(plan_dir)
    for k in (drop or []):
        rec.pop(k)
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "plan-approval.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


class TestPlanApprovalGate:
    """pr/ship/deploy demand a valid plan-approval; push must NOT change."""

    def _ready(self, root, **team_kw):
        d = _mk_plan(root, "260612-0800-feature-x")
        (d / "plan.md").write_text(
            "---\ntitle: x\nstatus: in_progress\n---\n\n# X\n\nIntent.\n\n"
            "## Phases\n\n| 1 | Pending |\n\n## Notes\n\n- n1\n",
            encoding="utf-8")
        (d / "phase-01-build.md").write_text(
            "---\nphase: 1\nstatus: pending\n---\n\n# P1\n\nfirst phase body\n",
            encoding="utf-8")
        _verification(d)
        _review(d)
        _mk_team(root, **team_kw)
        _critique(d)
        return d

    def test_push_does_not_require_plan_approval(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d)
        assert ac.check_stage("push", root) is None

    def test_pr_missing_plan_approval_blocks_naming_cli(self, root):
        self._ready(root)
        reason = ac.check_stage("pr", root)
        assert reason is not None and "plan-approval" in reason
        assert "plan_approval.py" in reason  # points at the writing CLI

    def test_pr_with_valid_approval_passes(self, root):
        d = self._ready(root)
        _approve(d, root)
        assert ac.check_stage("pr", root) is None

    def test_check_stage_explicit_plan_dir_bypasses_resolver(self, root):
        # H3: a NEWER in_progress plan B exists but plan_dir=A is passed → A is
        # judged (has receipts, passes), never B (which has none).
        a = self._ready(root)
        _approve(a, root)
        b = _mk_plan(root, "260612-0900-b-newer")  # newer, receiptless
        (b / "plan.md").write_text(
            "---\ntitle: b\nstatus: in_progress\n---\n\nb\n", encoding="utf-8")
        assert ac.check_stage("pr", root, plan_dir=a) is None      # judged A
        assert ac.check_stage("pr", root, plan_dir=b) is not None  # B would fail

    def test_check_stage_default_resolver_unchanged(self, root):
        # plan_dir=None (default) resolves as before — byte-for-byte the old path.
        d = self._ready(root)
        _approve(d, root)
        assert ac.check_stage("pr", root) == ac.check_stage("pr", root, plan_dir=None)

    def test_ship_and_deploy_also_require_it(self, root):
        d = self._ready(root)
        for stage in ("ship", "deploy"):
            reason = ac.check_stage(stage, root)
            assert reason is not None and "plan-approval" in reason
        _approve(d, root)
        for stage in ("ship", "deploy"):
            assert ac.check_stage(stage, root) is None

    def test_rejected_verdict_blocks(self, root):
        d = self._ready(root)
        _approve(d, root, verdict="REJECTED")
        reason = ac.check_stage("pr", root)
        assert reason is not None and "REJECTED" in reason

    def test_missing_required_field_blocks_naming_field(self, root):
        d = self._ready(root)
        _approve(d, root, drop=["plan_hash"])
        reason = ac.check_stage("pr", root)
        assert reason is not None and "plan_hash" in reason

    def test_self_approval_passes(self, root):
        # Personal-first SLIM: reviewer == author clears the gate — no roster, no
        # self-review block, no role rule.
        d = self._ready(root, reviewers=("user:alice",))
        _approve(d, root, reviewer="user:alice", author="user:alice")
        assert ac.check_stage("pr", root) is None

    def test_body_edit_after_approval_blocks_naming_changed_file(self, root):
        d = self._ready(root)
        _approve(d, root)
        p1 = d / "phase-01-build.md"
        p1.write_text(p1.read_text(encoding="utf-8").replace(
            "first phase body", "DIFFERENT body"), encoding="utf-8")
        reason = ac.check_stage("pr", root)
        assert reason is not None
        assert "phase-01-build.md" in reason  # names the drifted file
        assert "duyệt lại" in reason or "re-approve" in reason

    def test_frontmatter_and_phases_table_edits_do_not_block(self, root, monkeypatch):
        # The cook workflow legitimately mutates frontmatter status and the
        # plan.md Phases table after approval — the normalized hash must
        # carve exactly those out. (Status flips also change which plan is
        # ACTIVE, so the plan is pinned via env here — the gate question
        # under test is the hash, not plan resolution.)
        d = self._ready(root)
        _approve(d, root)
        monkeypatch.setenv("HARNESS_ACTIVE_PLAN", str(d))
        pm = d / "plan.md"
        pm.write_text(pm.read_text(encoding="utf-8").replace(
            "status: in_progress", "status: completed"), encoding="utf-8")
        p1 = d / "phase-01-build.md"
        p1.write_text(p1.read_text(encoding="utf-8").replace(
            "status: pending", "status: completed"), encoding="utf-8")
        pm2 = pm.read_text(encoding="utf-8").replace(
            "| 1 | Pending |", "| 1 | Completed |\n| 2 | New row |")
        pm.write_text(pm2, encoding="utf-8")
        assert ac.check_stage("pr", root) is None

    def test_stale_hash_blocks_with_reapprove_guidance(self, root):
        d = self._ready(root)
        _approve(d, root, stale_hash=True)
        reason = ac.check_stage("pr", root)
        assert reason is not None and "plan_approval.py" in reason

    def test_plan_approval_check_no_longer_reads_team(self, root):
        # Personal-first SLIM: the gate no longer loads team.yaml. A valid
        # self-approval PASSES even with the roster file removed / malformed.
        d = self._ready(root)
        _approve(d, root)
        team = root / "harness" / "data" / "team.yaml"
        team.write_text("reviewers: notalist\n", encoding="utf-8")  # malformed
        assert ac.check_stage("pr", root) is None
        team.unlink()  # absent entirely
        assert ac.check_stage("pr", root) is None


class TestPlannotatorReviewHint:
    """A MISSING review artifact resurfaces the Plannotator review option in
    the block reason — the reliable backstop so the option is never silently
    forgotten when the LLM skips offering it at the gate."""

    _MARK = "Plannotator"
    _RULE = "plannotator-review-gates.md"

    def test_missing_verification_reason_offers_plannotator(self, root):
        _mk_plan(root, "260612-0800-feature-x")
        reason = ac.check_stage("push", root)
        assert self._MARK in reason and self._RULE in reason

    def test_missing_review_decision_reason_offers_plannotator(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d)
        reason = ac.check_stage("pr", root)
        assert "review-decision" in reason
        assert self._MARK in reason and self._RULE in reason

    def test_missing_plan_approval_reason_offers_plannotator(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d)
        _review(d)
        reason = ac.check_stage("pr", root)
        assert "plan-approval" in reason
        assert self._MARK in reason and self._RULE in reason

    def test_present_but_failing_artifact_keeps_reason_clean(self, root):
        # The hint is for the MISSING case (no review happened yet). A present
        # artifact with a bad verdict is a content problem, not a "go review"
        # nudge — it must not carry the offer.
        d = _mk_plan(root, "260612-0800-feature-x")
        _verification(d, verdict="BLOCKED")
        reason = ac.check_stage("push", root)
        assert self._MARK not in reason


class TestCritiqueGateHint:
    """A stage requiring critique-consensus but missing it must name the exact
    command that produces the artifact (/hs:critique --gate) — not just 'create
    the json'. Mirrors plan-approval, which already names plan_approval.py."""

    def test_missing_critique_consensus_names_the_gate_command(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        reason = ac._check_artifact(d, "critique-consensus", root)
        assert "critique-consensus" in reason
        assert "hs:critique" in reason and "--gate" in reason

    def test_present_failing_critique_consensus_is_a_content_problem(self, root):
        # present-but-BLOCKED is a verdict problem, not a "go run it" nudge —
        # it must NOT carry the run-the-gate command hint.
        d = _mk_plan(root, "260612-0800-feature-x")
        _critique(d, verdict="BLOCKED")
        reason = ac._check_artifact(d, "critique-consensus", root)
        assert "verdict" in reason
        assert "--gate" not in reason


class TestPolicyLoad:
    def test_missing_policy_file_raises_loud(self, root, monkeypatch):
        monkeypatch.setenv("HARNESS_STAGE_POLICY", str(root / "nope.yaml"))
        with pytest.raises(Exception) as exc:
            ac.check_stage("push", root)
        msg = str(exc.value)
        assert "policy" in msg.lower()
        assert str(root / "nope.yaml") in msg          # names the RESOLVED path
        # remediation must point at the env override, not blindly the shipped
        # default — restoring harness/data/ does nothing while the env var points
        # elsewhere (the .harness-dev/ dev-override trap).
        assert "HARNESS_STAGE_POLICY" in msg

    def test_default_policy_declares_all_detector_stages(self):
        policy = ac.load_policy()
        for stage in ("commit", "push", "pr", "ship", "deploy"):
            assert stage in policy["stages"]


def _security_scan(plan_dir: Path, *, verdict="PASS", drop=None, findings=None):
    rec = {"verdict": verdict, "stage": "ship", "actor": "user:scanner",
           "ts": "2026-06-21T00:00:00Z",
           "findings": [] if findings is None else findings}
    for k in (drop or []):
        rec.pop(k, None)
    a = plan_dir / "artifacts"
    a.mkdir(parents=True, exist_ok=True)
    (a / "security-scan.json").write_text(json.dumps(rec), encoding="utf-8")


class TestSecurityScanGate:
    def test_pass_verdict_is_clean(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _security_scan(d, verdict="PASS")
        assert ac._check_artifact(d, "security-scan", root) is None

    def test_blocked_verdict_is_a_problem(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _security_scan(d, verdict="BLOCKED")
        reason = ac._check_artifact(d, "security-scan", root)
        assert reason and "security-scan" in reason and "PASS" in reason

    def test_missing_required_field_blocks(self, root):
        d = _mk_plan(root, "260612-0800-feature-x")
        _security_scan(d, drop=["findings"])
        reason = ac._check_artifact(d, "security-scan", root)
        assert reason and "findings" in reason

    def test_non_list_findings_blocks(self, root):
        # a malformed findings shape (a dict, not a list) must not silently clear a
        # PASS: iterating a dict yields its keys, never finding-dicts, so an open
        # critical would be missed and the PASS honored. Reject the shape instead.
        d = _mk_plan(root, "260612-0800-feature-x")
        _security_scan(d, verdict="PASS",
                       findings={"severity": "critical", "status": "open"})
        reason = ac._check_artifact(d, "security-scan", root)
        assert reason and "findings" in reason and "list" in reason

    def test_ships_off_no_stage_requires_it(self):
        import yaml
        repo = Path(__file__).resolve().parents[2]
        pol = yaml.safe_load((repo / "harness/data/stage-policy.yaml").read_text())
        for stage, spec in (pol.get("stages") or {}).items():
            assert "security-scan" not in (spec.get("requires") or []), \
                "security-scan must ship OFF (opt-in only): stage %r requires it" % stage


def test_security_scan_pass_with_open_high_blocks(root):
    d = _mk_plan(root, "260612-0800-feature-x")
    a = d / "artifacts"
    a.mkdir(parents=True, exist_ok=True)
    (a / "security-scan.json").write_text(json.dumps({
        "verdict": "PASS", "stage": "ship", "actor": "u", "ts": "t",
        "findings": [{"severity": "high", "status": "open", "finding": "x"}]}))
    reason = ac._check_artifact(d, "security-scan", root)
    assert reason and "open" in reason.lower()


def test_security_scan_pass_with_fixed_high_is_clean(root):
    d = _mk_plan(root, "260612-0800-feature-x")
    a = d / "artifacts"
    a.mkdir(parents=True, exist_ok=True)
    (a / "security-scan.json").write_text(json.dumps({
        "verdict": "PASS", "stage": "ship", "actor": "u", "ts": "t",
        "findings": [{"severity": "high", "status": "fixed", "finding": "x"}]}))
    assert ac._check_artifact(d, "security-scan", root) is None


def test_security_scan_uppercase_severity_still_blocks(root):
    # the consistency check must be case-insensitive (HIGH/Critical can't slip a PASS)
    d = _mk_plan(root, "260612-0800-feature-x")
    a = d / "artifacts"
    a.mkdir(parents=True, exist_ok=True)
    (a / "security-scan.json").write_text(json.dumps({
        "verdict": "PASS", "stage": "ship", "actor": "u", "ts": "t",
        "findings": [{"severity": "HIGH", "status": "Open", "finding": "x"}]}))
    assert ac._check_artifact(d, "security-scan", root) is not None


def test_active_plan_outside_plans_is_rejected(root, tmp_path, monkeypatch):
    # the gate's forgery defense guards plans/*/artifacts/ only — so a redirected
    # active plan pointing OUTSIDE plans/ (where an agent could pre-forge artifacts)
    # must be rejected, or it smuggles a forged PASS past a hard stage.
    outside = tmp_path / "forged"
    (outside / "artifacts").mkdir(parents=True)
    monkeypatch.setenv("HARNESS_ACTIVE_PLAN", str(outside))
    assert ac.resolve_active_plan(root) is None


def test_symlinked_artifacts_dir_is_rejected(root):
    # one level below the plan-dir check: a legit plan whose artifacts/ is a symlink
    # to a forged PASS planted outside plans/ must NOT clear a hard stage. (ln is not
    # a write the forgery guard recognizes, so the symlink itself plants undetected.)
    import os
    evil = root / "evil" / "artifacts"
    evil.mkdir(parents=True)
    (evil / "verification.json").write_text(json.dumps(
        {"verdict": "PASS", "stage": "push", "plan": "x", "actor": "a", "ts": "t",
         "checks": [{"name": "c", "status": "pass"}]}), encoding="utf-8")
    plan = root / "plans" / "260101-0000-forge"
    plan.mkdir(parents=True)
    (plan / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    os.symlink(evil, plan / "artifacts")
    reason = ac._check_artifact(plan, "verification", str(root))
    assert reason and "outside" in reason


def test_unknown_requires_kind_fails_loud(root):
    # an unrecognized requires-kind (a stage-policy typo) must fail loud, not pass a
    # present-but-meaningless {} file.
    d = _mk_plan(root, "260101-0000-typo")
    (d / "artifacts").mkdir()
    (d / "artifacts" / "foobar.json").write_text("{}", encoding="utf-8")
    reason = ac._check_artifact(d, "foobar", str(root))
    assert reason and "unknown artifact kind" in reason


# ── SSOT-YAML gate-artifact dual-parse (P4) ─────────────────────────────────

import yaml as _yaml  # noqa: E402


def _verification_yaml(plan_dir: Path, *, verdict="PASS", plan=None, checks=None):
    rec = {
        "stage": "push", "plan": plan or plan_dir.name, "actor": "user:alice",
        "ts": "2026-06-12T08:00:00+07:00",
        "checks": checks if checks is not None else [
            {"name": "pytest", "status": "PASS"}],
        "verdict": verdict,
    }
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "verification.yaml").write_text(_yaml.safe_dump(rec), encoding="utf-8")
    return rec


def test_verification_yaml_passes(root):
    d = _mk_plan(root, "260612-0800-yaml-pass")
    _verification_yaml(d)
    assert ac.check_stage("push", root) is None


def test_yaml_and_json_both_present_prefers_yaml(root):
    # legacy json says FAIL, yaml says PASS — gate must read yaml (preferred) and pass
    d = _mk_plan(root, "260612-0800-prefers-yaml")
    _verification(d, verdict="FAIL")          # writes verification.json
    _verification_yaml(d, verdict="PASS")     # writes verification.yaml
    assert ac.check_stage("push", root) is None
    rec, problem = ac._load_artifact(d, "verification")
    assert rec["verdict"] == "PASS"           # proves yaml won


def test_review_decision_yaml_fail_blocks(root):
    d = _mk_plan(root, "260612-0800-rd-yaml-fail")
    _verification_yaml(d)
    a = d / "artifacts"
    (a / "review-decision.yaml").write_text(
        _yaml.safe_dump({"verdict": "BLOCKED", "reviewer": "user:bob",
                         "role": "reviewer", "rationale": "no"}), encoding="utf-8")
    # 'pr' stage requires review-decision; a non-PASS verdict blocks
    reason = ac.check_stage("pr", root)
    assert reason is not None


def test_malformed_yaml_artifact_fails_closed(root):
    # a malformed/multi-doc verification.yaml must block with a message, not crash
    d = _mk_plan(root, "260612-0800-bad-yaml")
    a = d / "artifacts"
    a.mkdir()
    (a / "verification.yaml").write_text(
        "verdict: PASS\n\t bad: : indent\n: oops\n", encoding="utf-8")
    rec, problem = ac._load_artifact(d, "verification")
    assert rec is None and "YAML" in problem
    assert ac.check_stage("push", root) is not None  # blocks, no crash


def test_yaml_artifact_nondict_rejected(root):
    # a verification.yaml whose top level is a list (not a mapping) is rejected
    d = _mk_plan(root, "260612-0800-list-yaml")
    a = d / "artifacts"
    a.mkdir()
    (a / "verification.yaml").write_text("- a\n- b\n", encoding="utf-8")
    rec, problem = ac._load_artifact(d, "verification")
    assert rec is None and "object" in problem


def test_symlink_escape_yaml_blocked(root, tmp_path):
    # verification.yaml as a symlink pointing OUTSIDE artifacts/ must be refused
    d = _mk_plan(root, "260612-0800-symlink-yaml")
    a = d / "artifacts"
    a.mkdir()
    outside = tmp_path / "evil.yaml"
    outside.write_text(_yaml.safe_dump(
        {"stage": "push", "plan": d.name, "actor": "x", "ts": "t",
         "checks": [{"name": "c", "status": "PASS"}], "verdict": "PASS"}),
        encoding="utf-8")
    try:
        (a / "verification.yaml").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    rec, problem = ac._load_artifact(d, "verification")
    assert rec is None and "symlink escape" in problem


def test_rule_scan_yaml_consistency(root):
    # a rule-scan.yaml with a critical violation but verdict PASS is contradictory
    d = _mk_plan(root, "260612-0800-rulescan-yaml")
    a = d / "artifacts"
    a.mkdir()
    (a / "rule-scan.yaml").write_text(_yaml.safe_dump({
        "rules_applied": ["r1"], "verdict": "PASS", "reviewer": "user:bob",
        "ts": "2026-06-12T08:00:00+07:00",
        "violations": [{"id": "r1", "severity": "critical"}],
    }), encoding="utf-8")
    reason = ac._rule_scan_consistency(d)
    assert reason is not None  # contradiction caught from the .yaml form


def test_non_utf8_artifact_fails_closed_not_crash(root):
    # a non-UTF8 byte in an artifact must FAIL CLOSED (block reason), never raise
    # UnicodeDecodeError through the gate reader.
    d = _mk_plan(root, "260612-0800-nonutf8-art")
    a = d / "artifacts"
    a.mkdir()
    (a / "verification.yaml").write_bytes(b"verdict: PASS\nreviewer: \xff\xfe\n")
    rec, reason = ac._load_artifact(d, "verification")
    assert rec is None and reason and "unreadable" in reason


def test_non_utf8_plan_md_does_not_abort_scan(root):
    # one corrupt plan.md must NOT abort active-plan resolution for the others
    good = _mk_plan(root, "260612-0800-good", status="in_progress")
    bad = root / "plans" / "260612-0900-bad"   # sorts AFTER good → scanned first
    bad.mkdir(parents=True)
    bad.joinpath("plan.md").write_bytes(b"---\nstatus: in_progress \xff\n---\n")
    assert ac.resolve_active_plan(root) == good


def test_stage_floor_no_policy_is_noop_nonregression(root, monkeypatch):
    """Non-regression: with no review-policy.yaml, check_stage behaves exactly
    as before — the floor is a no-op and does not change any gate outcome.
    An absent HARNESS_REVIEW_POLICY env causes the floor to fail-soft (no-op)."""
    # Ensure no HARNESS_REVIEW_POLICY env so the floor receives no policy path
    monkeypatch.delenv("HARNESS_REVIEW_POLICY", raising=False)
    d = _mk_plan(root, "260626-0100-floor-nonreg")
    _verification(d)
    # push stage does not require review-decision; with a valid verification
    # and no review-policy, the gate must pass exactly as before
    assert ac.check_stage("push", root) is None


# --- Tier-2 architecture_review presence gate --------------------------------
# When the reviewed diff is STRUCTURAL (intersects standards.yaml
# drift.structural_globs), a hard stage requires review-decision to carry
# architecture_review.checked==true. Diff source = rule-scan.json:changed_files.
# Presence-gated, never sha-matched; absent rule-scan → abstain (no diff source).

def _rule_scan(plan_dir: Path, *, changed_files, verdict="PASS"):
    rec = {"rules_applied": [], "violations": [], "verdict": verdict,
           "reviewer": "user:bob", "ts": "2026-07-02T08:00:00+07:00",
           "changed_files": changed_files}
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "rule-scan.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def _review_arch(plan_dir: Path, *, checked=True, verdict="PASS"):
    rec = {"verdict": verdict, "reviewer": "user:bob", "role": "reviewer",
           "rationale": "looks correct",
           "architecture_review": {"checked": checked, "doc_sha": "deadbeef",
                                   "drift": []}}
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "review-decision.json").write_text(json.dumps(rec), encoding="utf-8")
    return rec


def _std_config(tmp_path: Path, monkeypatch, *, structural_globs="__DEFAULT__"):
    """Point HARNESS_STANDARDS_CONFIG at a fixture standards.yaml. Pass
    structural_globs=None to write a drift: section that OMITS the key (fallback
    test); a list to set it; the sentinel keeps a sane harness default."""
    import yaml
    if structural_globs == "__DEFAULT__":
        structural_globs = ["harness/hooks/**"]
    drift = {}
    if structural_globs is not None:
        drift["structural_globs"] = structural_globs
    cfg = tmp_path / "std-config.yaml"
    cfg.write_text(yaml.safe_dump({"drift": drift}, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("HARNESS_STANDARDS_CONFIG", str(cfg))
    return cfg


class TestArchitectureReviewGate:
    def test_arch_review_structural_missing_blocks(self, root, monkeypatch):
        d = _mk_plan(root, "260702-0800-arch-miss")
        _std_config(root, monkeypatch, structural_globs=["harness/hooks/**"])
        _rule_scan(d, changed_files=["harness/hooks/x.py"])
        _review(d)  # PASS but no architecture_review
        reason = ac._architecture_review_consistency(d, root)
        assert reason is not None
        assert "architecture" in reason.lower()

    def test_arch_review_structural_stamped_passes(self, root, monkeypatch):
        d = _mk_plan(root, "260702-0800-arch-stamp")
        _std_config(root, monkeypatch, structural_globs=["harness/hooks/**"])
        _rule_scan(d, changed_files=["harness/hooks/x.py"])
        _review_arch(d, checked=True)
        assert ac._architecture_review_consistency(d, root) is None

    def test_arch_review_nonstructural_passes(self, root, monkeypatch):
        d = _mk_plan(root, "260702-0800-arch-nonstruct")
        _std_config(root, monkeypatch, structural_globs=["harness/hooks/**"])
        _rule_scan(d, changed_files=["README.md"])
        _review(d)  # no stamp, but the diff is not structural → exempt
        assert ac._architecture_review_consistency(d, root) is None

    def test_arch_review_no_rulescan_abstains(self, root, monkeypatch):
        d = _mk_plan(root, "260702-0800-arch-norule")
        _std_config(root, monkeypatch, structural_globs=["harness/hooks/**"])
        _review(d)  # no rule-scan.json at all → no diff source
        assert ac._architecture_review_consistency(d, root) is None

    def test_arch_review_config_fallback(self, root, monkeypatch):
        # drift: present but structural_globs OMITTED → default globs (generic).
        d = _mk_plan(root, "260702-0800-arch-fallback")
        _std_config(root, monkeypatch, structural_globs=None)
        _review(d)
        # a generic tree (src/**) is structural under the default → block
        _rule_scan(d, changed_files=["src/core/x.py"])
        assert ac._architecture_review_consistency(d, root) is not None
        # this repo's harness/ layout is NOT in the generic default → exempt
        _rule_scan(d, changed_files=["harness/hooks/x.py"])
        assert ac._architecture_review_consistency(d, root) is None

    def test_arch_review_enforced_on_review_decision_stage(self, root, monkeypatch):
        # End-to-end on a stage that REQUIRES review-decision (pr): a structural
        # diff with no arch stamp blocks; stamping clears the arch leg. This is
        # where the gate belongs — the review-decision artifact is guaranteed to
        # exist because the stage already requires it.
        d = _mk_plan(root, "260702-0800-arch-pr")
        monkeypatch.setenv("HARNESS_ACTIVE_PLAN", str(d))
        _std_config(root, monkeypatch, structural_globs=["harness/hooks/**"])
        _mk_team(root)
        _approve(d, root)
        _verification(d)
        _rule_scan(d, changed_files=["harness/hooks/x.py"])
        _review(d)  # present PASS, no architecture_review
        reason = ac.check_stage("pr", root)
        assert reason is not None and "architecture" in reason.lower()
        _review_arch(d, checked=True)
        assert ac.check_stage("pr", root) is None

    def test_arch_gate_skips_stage_without_review_decision_requirement(self, root, monkeypatch):
        # push requires only verification, NOT review-decision. A structural diff
        # with no review-decision must not trip the arch gate at push — demanding
        # an artifact the stage never requires is a false-positive that blocks
        # honest work. Enforcement stays at pr/ship/merge/deploy (they require it).
        d = _mk_plan(root, "260702-0800-arch-push-skip")
        monkeypatch.setenv("HARNESS_ACTIVE_PLAN", str(d))
        _std_config(root, monkeypatch, structural_globs=["harness/hooks/**"])
        _verification(d)
        _rule_scan(d, changed_files=["harness/hooks/x.py"])
        # no review-decision artifact at all
        assert ac.check_stage("push", root) is None
