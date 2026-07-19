"""Contract for ci_wiring_check.py — the advisory check that closes lỗ #2:
the scaffold stamps a CI file into evals/ci/ and stops, so the parity/eval
workflow can sit there un-wired and never actually run. This check reads the
card's forge and verifies the stamped file is placed where that forge would
actually execute it (github: under .github/workflows/; gitlab: included from
the root .gitlab-ci.yml). Advisory by default (exit 0), --strict gates (exit 1),
and it is LOUD — never a silent pass — when it cannot determine wiring.
"""

import json
import subprocess
import sys

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "ci_wiring_check.py"

_GH_WORKFLOW = "name: Production Evals — widget\n\non:\n  push:\n    branches: [main]\n  pull_request:\n    branches: [main]\n\njobs:\n  evals:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
_GH_WORKFLOW_DISPATCH_ONLY = "name: Production Evals — widget\n\non:\n  workflow_dispatch:\n\njobs:\n  evals:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"


def _run(target, config=None, strict=False):
    args = [sys.executable, str(_SCRIPT), "--target", str(target)]
    if config is not None:
        args += ["--config", str(config)]
    if strict:
        args += ["--strict"]
    return subprocess.run(args, capture_output=True, text=True)


def _write_card(target, forge):
    evals = target / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    (evals / "eval_config.json").write_text(
        json.dumps({"forge": forge, "domain": "widget"}), encoding="utf-8")
    return evals / "eval_config.json"


def _stamp_ci(target, forge):
    ci = target / "evals" / "ci"
    ci.mkdir(parents=True, exist_ok=True)
    if forge == "github":
        (ci / "production-evals.yml").write_text(_GH_WORKFLOW, encoding="utf-8")
    else:
        (ci / ".gitlab-ci-evals.yml").write_text("stages: [eval]\n", encoding="utf-8")


def test_github_not_wired(tmp_path):
    _write_card(tmp_path, "github")
    _stamp_ci(tmp_path, "github")
    # no .github/workflows/ -> not wired
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "NOT_WIRED" in r.stdout
    assert ".github/workflows" in r.stdout


def test_github_wired(tmp_path):
    _write_card(tmp_path, "github")
    _stamp_ci(tmp_path, "github")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "production-evals.yml").write_text(_GH_WORKFLOW, encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "WIRED" in r.stdout and "NOT_WIRED" not in r.stdout


def test_github_wired_dispatch_only_caveat(tmp_path):
    # placed correctly BUT on: workflow_dispatch only -> WIRED with a caveat
    # that it does NOT run on push/PR (F6: "placed" != "runs automatically").
    _write_card(tmp_path, "github")
    _stamp_ci(tmp_path, "github")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "production-evals.yml").write_text(_GH_WORKFLOW_DISPATCH_ONLY, encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "WIRED" in r.stdout
    low = r.stdout.lower()
    assert "workflow_dispatch" in low or "push/pr" in low or "does not run" in low


def test_gitlab_not_wired(tmp_path):
    _write_card(tmp_path, "gitlab")
    _stamp_ci(tmp_path, "gitlab")
    # .gitlab-ci.yml absent -> not wired
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "NOT_WIRED" in r.stdout
    assert "include" in r.stdout.lower()


def test_gitlab_wired(tmp_path):
    _write_card(tmp_path, "gitlab")
    _stamp_ci(tmp_path, "gitlab")
    (tmp_path / ".gitlab-ci.yml").write_text(
        "include:\n  - local: 'evals/ci/.gitlab-ci-evals.yml'\n", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "WIRED" in r.stdout and "NOT_WIRED" not in r.stdout


def test_advisory_exit0_strict_exit1(tmp_path):
    _write_card(tmp_path, "github")
    _stamp_ci(tmp_path, "github")  # not wired
    default = _run(tmp_path)
    assert default.returncode == 0, "advisory default must not gate"
    strict = _run(tmp_path, strict=True)
    assert strict.returncode == 1, "--strict must gate on NOT_WIRED"


def test_gitlab_comment_mention_not_wired(tmp_path):
    # the eval filename appearing only in a COMMENT is not a real include
    _write_card(tmp_path, "gitlab")
    _stamp_ci(tmp_path, "gitlab")
    (tmp_path / ".gitlab-ci.yml").write_text(
        "# reminder: wire .gitlab-ci-evals.yml one day\nstages: [test]\n", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "NOT_WIRED" in r.stdout


def test_github_push_list_form_no_false_caveat(tmp_path):
    # `on: [push, workflow_dispatch]` DOES run on push -> WIRED, no dispatch-only caveat
    _write_card(tmp_path, "github")
    _stamp_ci(tmp_path, "github")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "production-evals.yml").write_text(
        "name: Production Evals — widget\non: [push, workflow_dispatch]\njobs:\n  e:\n    runs-on: x\n    steps: []\n",
        encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "WIRED" in r.stdout
    low = r.stdout.lower()
    assert "does not run" not in low and "workflow_dispatch only" not in low


def test_github_dispatch_only_despite_push_comment(tmp_path):
    # a genuinely dispatch-only workflow with a `# push:` COMMENT must still get
    # the caveat (the comment must not suppress it)
    _write_card(tmp_path, "github")
    _stamp_ci(tmp_path, "github")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "production-evals.yml").write_text(
        "name: Production Evals — widget\non:\n  workflow_dispatch:\n# TODO: add push: trigger later\njobs:\n  e:\n    runs-on: x\n    steps: []\n",
        encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "WIRED" in r.stdout
    low = r.stdout.lower()
    assert "workflow_dispatch" in low or "does not run" in low or "push/pr" in low


def test_loud_when_indeterminate(tmp_path):
    # no card, no CI file at all -> must be LOUD (INDETERMINATE), never a silent
    # pass (the importorskip-silent failure mode this whole fix targets).
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "INDETERMINATE" in r.stdout
    strict = _run(tmp_path, strict=True)
    assert strict.returncode == 1


def test_gitlab_inline_comment_mention_not_wired(tmp_path):
    # the eval filename only in an INLINE trailing comment is not a real include
    _write_card(tmp_path, "gitlab")
    _stamp_ci(tmp_path, "gitlab")
    (tmp_path / ".gitlab-ci.yml").write_text(
        "include:\n  - local: other.yml  # later: .gitlab-ci-evals.yml\n", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "NOT_WIRED" in r.stdout


def test_github_push_in_step_name_still_gets_dispatch_caveat(tmp_path):
    # 'push' appearing in a job/step name must NOT suppress the dispatch-only
    # caveat -- only a real on: trigger counts.
    _write_card(tmp_path, "github")
    _stamp_ci(tmp_path, "github")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "production-evals.yml").write_text(
        "name: Production Evals — widget\non:\n  workflow_dispatch:\njobs:\n  e:\n    runs-on: x\n    steps:\n      - name: docker push\n        run: echo hi\n",
        encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    low = r.stdout.lower()
    assert "workflow_dispatch" in low or "does not run" in low or "push/pr" in low
