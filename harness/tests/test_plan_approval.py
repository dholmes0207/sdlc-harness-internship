"""test_plan_approval.py — role-consistency check on attribution + the
normalized plan-dir hash behind the plan-approval artifact.

This is NOT authentication: actor strings are env-derived and spoofable by
design. The check raises the price of self-approval (reviewer must be in the
tracked roster and a different person than the author, with `/agent:*`
personas collapsing to their user), it does not prove identity.

The hash is over NORMALIZED plan content: YAML frontmatter is stripped from
every file and the `## Phases` section is stripped from plan.md, because the
cook workflow legitimately mutates exactly those two regions after approval.
Hash-the-bytes would go stale on every run and train reviewers to
rubber-stamp; the body — the thing approval is about — stays pinned.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plan_approval as pa  # noqa: E402


def _mk_plan(root: Path, name="260612-0900-w2-thing", author=None, with_graph=True):
    d = root / "plans" / name
    d.mkdir(parents=True)
    fm_author = ("author: %s\n" % author) if author else ""
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: pending\n%s---\n\n# Thing\n\nBody intent.\n\n"
        "## Phases\n\n| Phase | Status |\n|---|---|\n| 1 | Pending |\n\n"
        "## Acceptance\n\n- works\n" % (name, fm_author),
        encoding="utf-8")
    (d / "phase-01-build.md").write_text(
        "---\nphase: 1\nstatus: pending\n---\n\n# Phase 1\n\nDo the thing.\n",
        encoding="utf-8")
    (d / "phase-02-test.md").write_text(
        "---\nphase: 2\nstatus: pending\n---\n\n# Phase 2\n\nProve it.\n",
        encoding="utf-8")
    # The phase-DAG sidecar is a mandatory plan artifact; write_approval refuses
    # APPROVED without it. Default it on; the backcompat / no-sidecar cases pass
    # with_graph=False explicitly.
    if with_graph:
        (d / "plan-graph.yaml").write_text(
            "edges:\n  - {from: P1, to: P2}\n"
            "subtasks:\n  P1: {files_to_create: [], files_to_modify: []}\n"
            "  P2: {files_to_create: [], files_to_modify: []}\n",
            encoding="utf-8")
    return d


def _mk_team(root: Path, reviewers=("user:rev@x",), allow_self_review=False):
    d = root / "harness" / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "team.yaml").write_text(
        "reviewers: [%s]\nallow_self_review: %s\nclaims: {lease_s: 14400}\n"
        % (", ".join('"%s"' % r for r in reviewers),
           "true" if allow_self_review else "false"),
        encoding="utf-8")


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _mk_team(tmp_path)
    return tmp_path


# ---------- normalize ----------

class TestNormalizeActor:
    def test_agent_suffix_is_cut(self):
        assert pa.normalize_actor("user:a@x/agent:reviewer") == "user:a@x"

    def test_plain_user_unchanged(self):
        assert pa.normalize_actor("user:a@x") == "user:a@x"

    def test_ci_unchanged(self):
        assert pa.normalize_actor("ci") == "ci"

    def test_two_personas_of_one_user_collapse_to_same(self):
        a = pa.normalize_actor("user:a@x/agent:planner")
        b = pa.normalize_actor("user:a@x/agent:reviewer")
        assert a == b


# ---------- role rule (4 quadrants × solo mode) ----------

# TestRoleRule removed: personal-first SLIM has no roster/self-review/role rule
# (check_role deleted). Self-approval is now allowed — see TestWriteApproval and
# TestCLI below. normalize_actor stays as a public helper (TestNormalizeActor
# pins its behavior).


# ---------- normalized plan-dir hash ----------

class TestPlanHash:
    def test_hash_is_12_hex(self, root):
        d = _mk_plan(root)
        h = pa.plan_hash(d)
        assert len(h) == 12 and int(h, 16) >= 0

    def test_frontmatter_status_flip_does_not_change_hash(self, root):
        d = _mk_plan(root)
        before = pa.plan_hash(d)
        for f in (d / "plan.md", d / "phase-01-build.md"):
            f.write_text(f.read_text(encoding="utf-8").replace(
                "status: pending", "status: in_progress"), encoding="utf-8")
        assert pa.plan_hash(d) == before

    def test_phases_table_edit_in_plan_md_does_not_change_hash(self, root):
        d = _mk_plan(root)
        before = pa.plan_hash(d)
        pm = d / "plan.md"
        pm.write_text(pm.read_text(encoding="utf-8").replace(
            "| 1 | Pending |", "| 1 | Completed ✅ |\n| 2 | In progress |"),
            encoding="utf-8")
        assert pa.plan_hash(d) == before

    def test_body_edit_in_plan_md_changes_hash(self, root):
        d = _mk_plan(root)
        before = pa.plan_hash(d)
        pm = d / "plan.md"
        pm.write_text(pm.read_text(encoding="utf-8").replace(
            "Body intent.", "Body intent CHANGED."), encoding="utf-8")
        assert pa.plan_hash(d) != before

    def test_applies_rules_in_plan_fm_no_drift(self, root):
        # passive-provenance convention: adding `applies_rules: [STD-...]` to the
        # plan frontmatter must NOT change plan_hash (frontmatter is stripped), so
        # stamping provenance never forces a re-approval.
        d = _mk_plan(root)
        before = pa.plan_hash(d)
        pm = d / "plan.md"
        pm.write_text(pm.read_text(encoding="utf-8").replace(
            "status: pending\n",
            "status: pending\napplies_rules:\n  - STD-AUTH-RG1-R1\n", 1),
            encoding="utf-8")
        assert pa.plan_hash(d) == before

    def test_body_edit_in_phase_file_changes_hash(self, root):
        d = _mk_plan(root)
        before = pa.plan_hash(d)
        p1 = d / "phase-01-build.md"
        p1.write_text(p1.read_text(encoding="utf-8").replace(
            "Do the thing.", "Do a different thing."), encoding="utf-8")
        assert pa.plan_hash(d) != before

    def test_new_phase_file_changes_hash(self, root):
        d = _mk_plan(root)
        before = pa.plan_hash(d)
        (d / "phase-03-extra.md").write_text(
            "---\nphase: 3\n---\n\n# Phase 3\n\nNew scope.\n", encoding="utf-8")
        assert pa.plan_hash(d) != before

    def test_phases_section_outside_plan_md_is_not_stripped(self, root):
        # Only plan.md owns a legitimately-mutating ## Phases section; the
        # same heading inside a phase file is body and stays pinned.
        d = _mk_plan(root)
        p1 = d / "phase-01-build.md"
        p1.write_text(p1.read_text(encoding="utf-8")
                      + "\n## Phases\n\nnarrative\n", encoding="utf-8")
        before = pa.plan_hash(d)
        p1.write_text(p1.read_text(encoding="utf-8").replace(
            "narrative", "narrative CHANGED"), encoding="utf-8")
        assert pa.plan_hash(d) != before

    def test_file_hashes_names_each_file(self, root):
        d = _mk_plan(root)
        fh = pa.file_hashes(d)
        assert set(fh) == {"plan.md", "phase-01-build.md", "phase-02-test.md",
                           "plan-graph.yaml"}
        assert all(len(v) == 12 for v in fh.values())

    def test_file_hashes_omits_sidecar_when_absent(self, root):
        # Backcompat: a plan without the sidecar still hashes its md files (the
        # sidecar is only folded in when present).
        d = _mk_plan(root, with_graph=False)
        fh = pa.file_hashes(d)
        assert set(fh) == {"plan.md", "phase-01-build.md", "phase-02-test.md"}


# ---------- write_approval (lib) ----------

class TestWriteApproval:
    def test_writes_schema_v1_artifact_with_hash_and_trace(self, root, monkeypatch):
        monkeypatch.setenv("HARNESS_USER", "rev@x")
        d = _mk_plan(root, author="user:auth@x")
        out = pa.write_approval(d, verdict="APPROVED", rationale="solid")
        assert out["ok"], out
        rec = pa._read_record(d / "artifacts" / "plan-approval.yaml")
        assert rec["schema"] == "plan-approval/v1"
        assert rec["plan"] == d.name
        assert rec["plan_hash"] == pa.plan_hash(d)
        assert rec["author"] == "user:auth@x"
        assert rec["reviewer"].startswith("user:rev@x")
        assert rec["verdict"] == "APPROVED"
        assert rec["rationale"] == "solid"
        assert "ts" in rec
        assert set(rec["file_hashes"]) == set(pa.file_hashes(d))
        trace = "".join(p.read_text(encoding="utf-8") for p in
                        (root / "state" / "trace").glob("trace-*.jsonl"))
        assert "plan_approval" in trace

    def test_self_approval_allowed(self, root, monkeypatch):
        # Personal-first SLIM: reviewer == author is now ALLOWED (no roster, no
        # self-review block — self-approval is deliberate discipline).
        monkeypatch.setenv("HARNESS_USER", "auth@x")  # reviewer == author
        d = _mk_plan(root, author="user:auth@x")
        out = pa.write_approval(d, verdict="APPROVED", rationale="lgtm")
        assert out["ok"], out
        assert (d / "artifacts" / "plan-approval.yaml").exists()

    def test_author_resolution_falls_back_to_explicit_arg(self, root, monkeypatch):
        monkeypatch.setenv("HARNESS_USER", "rev@x")
        d = _mk_plan(root)  # no author frontmatter
        out = pa.write_approval(d, verdict="APPROVED", rationale="r")
        assert out["ok"] is False and "--author" in out["error"]
        out2 = pa.write_approval(d, verdict="APPROVED", rationale="r",
                                 author="user:auth@x")
        assert out2["ok"]

    def test_never_writes_with_empty_author(self, root, monkeypatch):
        monkeypatch.setenv("HARNESS_USER", "rev@x")
        d = _mk_plan(root)
        out = pa.write_approval(d, verdict="APPROVED", rationale="r", author="")
        assert out["ok"] is False
        assert not (d / "artifacts" / "plan-approval.yaml").exists()

    def test_approved_refused_without_plan_graph(self, root, monkeypatch):
        # The phase-DAG sidecar is mandatory for APPROVED — refuse, write nothing.
        monkeypatch.setenv("HARNESS_USER", "rev@x")
        d = _mk_plan(root, author="user:auth@x", with_graph=False)
        out = pa.write_approval(d, verdict="APPROVED", rationale="solid")
        assert out["ok"] is False
        assert "plan-graph.yaml" in out["error"]
        assert not (d / "artifacts" / "plan-approval.yaml").exists()

    def test_rejected_allowed_without_plan_graph(self, root, monkeypatch):
        # A REJECTED verdict needs no sidecar — you can reject an incomplete plan.
        monkeypatch.setenv("HARNESS_USER", "rev@x")
        d = _mk_plan(root, author="user:auth@x", with_graph=False)
        out = pa.write_approval(d, verdict="REJECTED", rationale="missing graph")
        assert out["ok"], out

    @pytest.mark.parametrize("verdict,expected", [
        ("approved", "APPROVED"), ("Approved", "APPROVED"),
        ("ApprOvEd", "APPROVED"), (" approved ", "APPROVED"),
        ("rejected", "REJECTED"), ("ReJeCtEd", "REJECTED"),
    ])
    def test_verdict_is_case_insensitive(self, root, monkeypatch, verdict, expected):
        # A valid verdict in any case must NOT be a false-positive reject; it
        # normalizes to the canonical upper form and records that.
        monkeypatch.setenv("HARNESS_USER", "rev@x")
        d = _mk_plan(root, author="user:auth@x")
        out = pa.write_approval(d, verdict=verdict, rationale="r")
        assert out["ok"], out
        assert out["record"]["verdict"] == expected

    def test_garbage_verdict_still_refused(self, root, monkeypatch):
        monkeypatch.setenv("HARNESS_USER", "rev@x")
        d = _mk_plan(root, author="user:auth@x")
        out = pa.write_approval(d, verdict="maybe", rationale="r")
        assert out["ok"] is False and "APPROVED or REJECTED" in out["error"]


# ---------- CLI ----------

class TestCLI:
    def _env(self, root, user="rev@x"):
        env = dict(os.environ)
        env["HARNESS_ROOT"] = str(root)
        env["HARNESS_STATE_DIR"] = str(root / "state")
        env["HARNESS_USER"] = user
        env.pop("HARNESS_AGENT", None)
        return env

    def test_cli_approves_and_exits_zero(self, root):
        d = _mk_plan(root, author="user:auth@x")
        out = subprocess.run(
            [sys.executable, str(_SCRIPTS / "plan_approval.py"),
             "--plan", str(d), "--verdict", "APPROVED",
             "--rationale", "reviewed end to end"],
            capture_output=True, text=True, env=self._env(root), timeout=30)
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout)["ok"]
        assert (d / "artifacts" / "plan-approval.yaml").exists()

    def test_cli_self_approval_allowed(self, root):
        # Personal-first SLIM: author == reviewer approves cleanly (exit 0, artifact
        # written) — self-approval is deliberate discipline, not blocked.
        d = _mk_plan(root, author="user:rev@x")  # author == reviewer
        out = subprocess.run(
            [sys.executable, str(_SCRIPTS / "plan_approval.py"),
             "--plan", str(d), "--verdict", "APPROVED", "--rationale", "self-reviewed"],
            capture_output=True, text=True, env=self._env(root), timeout=30)
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout)["ok"]
        assert (d / "artifacts" / "plan-approval.yaml").exists()


class TestArgErgonomics:
    """--plan accepts a plan.md path OR a folder; --verdict is case-insensitive
    (both are ergonomic fixes so a valid intent is never a false-positive error)."""

    def _run(self, root, monkeypatch, plan_arg, verdict):
        monkeypatch.setenv("HARNESS_USER", "rev@x")
        rc = pa.main(["--plan", str(plan_arg), "--verdict", verdict,
                      "--rationale", "reviewed"])
        return rc

    def test_plan_arg_accepts_plan_md_file(self, root, monkeypatch, capsys):
        d = _mk_plan(root, author="user:auth@x")
        rc = self._run(root, monkeypatch, d / "plan.md", "APPROVED")
        assert rc == 0, capsys.readouterr().out
        assert json.loads(capsys.readouterr().out)["ok"]
        assert (d / "artifacts" / "plan-approval.yaml").exists()

    def test_plan_arg_still_accepts_folder(self, root, monkeypatch, capsys):
        d = _mk_plan(root, author="user:auth@x")
        rc = self._run(root, monkeypatch, d, "APPROVED")
        assert rc == 0, capsys.readouterr().out

    def test_verdict_lowercase_via_cli(self, root, monkeypatch, capsys):
        d = _mk_plan(root, author="user:auth@x")
        rc = self._run(root, monkeypatch, d, "approved")
        assert rc == 0, capsys.readouterr().out
        assert json.loads(capsys.readouterr().out)["record"]["verdict"] == "APPROVED"

    def test_verdict_mixed_case_via_cli(self, root, monkeypatch, capsys):
        d = _mk_plan(root, author="user:auth@x")
        rc = self._run(root, monkeypatch, d / "plan.md", "ApprOvEd")
        assert rc == 0, capsys.readouterr().out
