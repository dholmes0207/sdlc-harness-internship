"""test_plannotator_surface.py — optional Plannotator review-surface adapter.

Detect the external `plannotator` binary, env-gate out of CI/headless,
launch it on an artifact, and normalize the outcome. The surface is
fail-open: a missing binary, a hostile env, or garbled output degrade to a
status the caller can branch on — never an exception.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plannotator_surface as ps  # noqa: E402


def _shquote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _fake_binary(tmp_path, *, stdout="", stderr="", code=0) -> Path:
    """A stand-in `plannotator` that echoes fixed output and exits `code`."""
    p = tmp_path / "plannotator"
    p.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s' " + _shquote(stdout) + "\n"
        "printf '%s' " + _shquote(stderr) + " 1>&2\n"
        "exit " + str(code) + "\n",
        encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _recording_binary(tmp_path, args_file, *, stdout="", code=0) -> Path:
    """A stand-in `plannotator` that records its argv (one per line) to
    `args_file`, then echoes `stdout` and exits `code`."""
    p = tmp_path / "plannotator"
    p.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do printf "%s\\n" "$a"; done > '
        + _shquote(str(args_file)) + "\n"
        "printf '%s' " + _shquote(stdout) + "\n"
        "exit " + str(code) + "\n",
        encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Deterministic gate: strip CI/disable/override markers the host may carry.
    for k in ("CI", "GITLAB_CI", "GITHUB_ACTIONS", "PLANNOTATOR_DISABLE",
              "PLANNOTATOR_BINARY"):
        monkeypatch.delenv(k, raising=False)


class TestDetect:
    def test_path_lookup(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path)
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep
                           + os.environ.get("PATH", ""))
        assert ps.detect() == str(b)

    def test_binary_env_override(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path)
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        assert ps.detect() == str(b)

    def test_absent_returns_none(self, monkeypatch):
        monkeypatch.setenv("PATH", "")
        assert ps.detect() is None


class TestEnvAllows:
    @pytest.mark.parametrize(
        "var", ["CI", "GITLAB_CI", "GITHUB_ACTIONS", "PLANNOTATOR_DISABLE"])
    def test_blocked_in_ci_or_disabled(self, monkeypatch, var):
        monkeypatch.setenv(var, "1")
        assert ps.env_allows() is False

    def test_clean_local_allows(self):
        assert ps.env_allows() is True


class TestRunAnnotate:
    def _bin(self, tmp_path, monkeypatch, payload):
        b = _fake_binary(tmp_path, stdout=payload)
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        return b

    def test_approved(self, tmp_path, monkeypatch):
        self._bin(tmp_path, monkeypatch, json.dumps({"decision": "approved"}))
        assert ps.run("annotate", "plan.md") == {"status": "approved"}

    def test_dismissed(self, tmp_path, monkeypatch):
        self._bin(tmp_path, monkeypatch, json.dumps({"decision": "dismissed"}))
        assert ps.run("annotate", "plan.md") == {"status": "dismissed"}

    def test_annotated_carries_feedback(self, tmp_path, monkeypatch):
        self._bin(tmp_path, monkeypatch,
                  json.dumps({"decision": "annotated", "feedback": "fix X"}))
        assert ps.run("annotate", "plan.md") == {
            "status": "annotated", "feedback": "fix X"}

    def test_empty_stdout_is_dismissed(self, tmp_path, monkeypatch):
        self._bin(tmp_path, monkeypatch, "")
        assert ps.run("annotate", "plan.md") == {"status": "dismissed"}

    def test_garbage_output_is_error_not_raise(self, tmp_path, monkeypatch):
        self._bin(tmp_path, monkeypatch, "not json at all")
        assert ps.run("annotate", "plan.md")["status"] == "error"

    def test_nonzero_exit_is_error(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path, stdout="", stderr="boom", code=3)
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        assert ps.run("annotate", "plan.md")["status"] == "error"


class TestRunGating:
    def test_unavailable_when_absent(self, monkeypatch):
        monkeypatch.setenv("PATH", "")
        assert ps.run("annotate", "plan.md") == {"status": "unavailable"}

    def test_skipped_in_ci(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path)
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        monkeypatch.setenv("CI", "1")
        assert ps.run("annotate", "plan.md") == {"status": "skipped"}

    def test_unknown_mode_is_error(self):
        assert ps.run("frobnicate", "x")["status"] == "error"


class TestRunReview:
    def test_review_passes_through_plaintext(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path, stdout="LGTM, ship it")
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        out = ps.run("review", "HEAD")
        assert out["status"] == "review_text"
        assert "LGTM" in out["feedback"]


class TestTraceFailOpen:
    def test_runs_without_trace_module(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ps, "trace_log", None)
        b = _fake_binary(tmp_path, stdout=json.dumps({"decision": "approved"}))
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        assert ps.run("annotate", "plan.md") == {"status": "approved"}

    def test_trace_called_on_result(self, tmp_path, monkeypatch):
        seen = []
        monkeypatch.setattr(ps, "_trace", lambda event, **kw: seen.append(event))
        b = _fake_binary(tmp_path, stdout=json.dumps({"decision": "approved"}))
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        ps.run("annotate", "plan.md")
        assert "result" in seen


class TestCli:
    def test_unavailable_prints_guide_to_stderr(self, monkeypatch, capsys):
        monkeypatch.setenv("PATH", "")
        rc = ps.main(["annotate", "plan.md"])
        captured = capsys.readouterr()
        assert rc == 0
        assert json.loads(captured.out)["status"] == "unavailable"
        assert "plannotator.ai" in captured.err

    def test_missing_args_usage(self, capsys):
        assert ps.main(["annotate"]) == 2


def _plandir(tmp_path, phase_files=()):
    """A plan directory: plan.md plus optional sibling phase files."""
    d = tmp_path / "260618-x"
    d.mkdir()
    (d / "plan.md").write_text("---\nid: x\n---\n# plan\n", encoding="utf-8")
    for pf in phase_files:
        (d / pf).write_text("# " + pf + "\n", encoding="utf-8")
    return d


class TestPlanPhaseFiles:
    """A multi-phase plan.md exposes its `phase*.md` siblings, sorted."""

    def test_lists_sorted_phase_siblings(self, tmp_path):
        d = _plandir(tmp_path, ["phase-b-y.md", "phase-a-x.md"])
        out = ps._plan_phase_files(str(d / "plan.md"))
        assert [os.path.basename(p) for p in out] == [
            "phase-a-x.md", "phase-b-y.md"]

    def test_single_file_plan_has_no_phases(self, tmp_path):
        d = _plandir(tmp_path)
        assert ps._plan_phase_files(str(d / "plan.md")) == []

    def test_non_planmd_target_has_no_phases(self, tmp_path):
        d = _plandir(tmp_path, ["phase-a-x.md"])
        assert ps._plan_phase_files(str(d / "phase-a-x.md")) == []

    def test_missing_file_has_no_phases(self):
        assert ps._plan_phase_files("nope/plan.md") == []


class TestRunAnnotateGate:
    """annotate always enables the Approve button (--gate); a multi-phase plan
    is annotated as its directory so the folder browser surfaces every phase."""

    def test_multiphase_annotates_directory_with_gate(self, tmp_path,
                                                      monkeypatch):
        d = _plandir(tmp_path, ["phase-a-x.md"])
        args_file = tmp_path / "args.txt"
        b = _recording_binary(tmp_path, args_file,
                              stdout=json.dumps({"decision": "approved"}))
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        out = ps.run("annotate", str(d / "plan.md"))
        assert out == {"status": "approved"}
        recorded = args_file.read_text(encoding="utf-8").splitlines()
        assert "--gate" in recorded            # Approve button enabled
        assert "--json" in recorded
        plan_dir = os.path.dirname(os.path.abspath(str(d / "plan.md")))
        assert plan_dir in recorded            # the directory (folder browser)
        assert str(d / "plan.md") not in recorded

    def test_single_file_plan_annotated_directly_with_gate(self, tmp_path,
                                                           monkeypatch):
        d = _plandir(tmp_path)  # plan.md alone, no phases
        args_file = tmp_path / "args.txt"
        b = _recording_binary(tmp_path, args_file,
                              stdout=json.dumps({"decision": "approved"}))
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        ps.run("annotate", str(d / "plan.md"))
        recorded = args_file.read_text(encoding="utf-8").splitlines()
        assert "--gate" in recorded
        assert str(d / "plan.md") in recorded   # the file itself, no temp doc

    def test_review_mode_has_no_gate(self, tmp_path, monkeypatch):
        args_file = tmp_path / "args.txt"
        b = _recording_binary(tmp_path, args_file, stdout="LGTM")
        monkeypatch.setenv("PLANNOTATOR_BINARY", str(b))
        ps.run("review", "HEAD~1..HEAD")
        recorded = args_file.read_text(encoding="utf-8").splitlines()
        assert "--gate" not in recorded
        assert "HEAD~1..HEAD" in recorded
