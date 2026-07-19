"""Tests for standards_compliance_run — execute type-1 (shell) compliance checks.

A rule's compliance_checks split into two kinds: type-1 is shell-executable
(`mypy --strict`, `grep -q ...`, `true`) and runs for real, giving a
deterministic pass/fail; type-2 is LLM-judged and is never executed — the
machine only enforces format + coverage for it, NOT correctness. A bare string
check is back-compat shorthand for a judged check. The runner is advisory-first:
it reports results and always exits 0, and it is NEVER wired into a hook (it
would be arbitrary command execution from config).
"""

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import standards_compliance_run as scr  # noqa: E402
import trust_store  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def trust(tmp_path, monkeypatch):
    """Return a helper that trusts a path in an isolated per-test trust store.

    The shell-exec compliance runner is trust-gated (a YAML-sourced command only
    runs against a repo the operator explicitly vetted); tests that exercise real
    execution must trust their cwd first."""
    store = tmp_path / "trust.json"
    monkeypatch.setenv("HARNESS_TRUST_STORE", str(store))

    def _trust(path):
        trust_store.add_trust(str(path))
        return path
    return _trust


def _area_with_checks(checks_yaml: str) -> str:
    return f"""id: STD-Q
type: std_area
title: "Quality"
status: approved
owner: eng
version: 1.0.0
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-Q-RG1
    title: "Checks"
    status: approved
    rules:
      - id: STD-Q-RG1-R1
        title: "A rule"
        status: approved
        compliance_checks:
{checks_yaml}
"""


def _tree(root: Path, checks_yaml: str) -> Path:
    std = root / "harness" / "standards"
    (std / "areas").mkdir(parents=True, exist_ok=True)
    (std / "areas" / "STD-Q.std.yaml").write_text(
        _area_with_checks(checks_yaml), encoding="utf-8")
    return root


# ── classification ──────────────────────────────────────────────────────────

def test_string_check_backcompat():
    norm = scr.classify_check("assert no eval() in code")
    assert norm["type"] == "judged"
    assert "eval()" in norm["text"]


def test_shell_dict_classified():
    norm = scr.classify_check({"type": "shell", "cmd": "true"})
    assert norm["type"] == "shell"
    assert norm["cmd"] == "true"


# ── execution ───────────────────────────────────────────────────────────────

def test_shell_check_runs_and_passes(tmp_path, trust):
    trust(tmp_path)
    res = scr.run_check(scr.classify_check({"type": "shell", "cmd": "true"}), cwd=tmp_path)
    assert res["result"] == "pass"


def test_shell_check_fails_on_nonzero(tmp_path, trust):
    trust(tmp_path)
    res = scr.run_check(scr.classify_check({"type": "shell", "cmd": "false"}), cwd=tmp_path)
    assert res["result"] == "fail"


# ── trust gate (R2 / shell-exec only on a vetted repo) ──────────────────────

def test_shell_skipped_when_untrusted(tmp_path, trust, monkeypatch):
    # cwd is NOT trusted → the shell command must NOT run; result is skipped.
    monkeypatch.setenv("HARNESS_TRUST_STORE", str(tmp_path / "empty.json"))
    called = {"n": 0}
    real = scr.mechanical_runner._run_shell

    def _spy(*a, **k):
        called["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(scr.mechanical_runner, "_run_shell", _spy)

    res = scr.run_check(scr.classify_check({"type": "shell", "cmd": "true"}), cwd=tmp_path)
    assert res["result"] == "skipped"
    assert res.get("executed") is False
    assert called["n"] == 0, "an untrusted cwd must not execute the shell command"


def test_shell_runs_when_trusted(tmp_path, trust):
    trust(tmp_path)
    res = scr.run_check(scr.classify_check({"type": "shell", "cmd": "true"}), cwd=tmp_path)
    assert res["result"] == "pass" and res.get("executed") is True


def test_trust_realpath_symmetry(tmp_path, trust):
    # add_trust and is_trusted both realpath, so a `..`-laden path to the same
    # root still matches; a subdir does NOT (fail-closed).
    trust(tmp_path)
    devious = tmp_path / "sub" / ".."
    (tmp_path / "sub").mkdir()
    res = scr.run_check(scr.classify_check({"type": "shell", "cmd": "true"}), cwd=devious)
    assert res["result"] == "pass", "realpath-equal root must stay trusted"

    sub = tmp_path / "sub"
    res_sub = scr.run_check(scr.classify_check({"type": "shell", "cmd": "true"}), cwd=sub)
    assert res_sub["result"] == "skipped", "a subdir of a trusted root is not trusted"


def test_judged_unaffected_by_trust(tmp_path):
    # judged checks never execute, so the trust gate does not touch them.
    res = scr.run_check(scr.classify_check("a human reasons about this"), cwd=tmp_path)
    assert res["result"] == "judged" and res.get("executed") is False


def test_string_check_not_executed(tmp_path):
    # a bare string must NOT be executed as a shell command (it is judged)
    res = scr.run_check(scr.classify_check("rm -rf /"), cwd=tmp_path)
    assert res["result"] == "judged"
    assert res.get("executed") is False


# ── runner over a tree (advisory-first) ─────────────────────────────────────

def test_runner_reports_per_rule(tmp_path, trust):
    root = _tree(tmp_path, '          - {type: shell, cmd: "true"}\n')
    trust(root)
    report = scr.run_root(root)
    rows = [r for r in report["checks"] if r["rule_id"] == "STD-Q-RG1-R1"]
    assert rows and rows[0]["result"] == "pass"


def test_runner_advisory_not_block(tmp_path, trust, capsys):
    # a failing shell check must NOT make the runner exit non-zero (advisory)
    root = _tree(tmp_path, '          - {type: shell, cmd: "false"}\n')
    trust(root)
    rc = scr.main(["--root", str(root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fail" in out.lower()


def test_runner_skips_shell_on_untrusted_root_exit0(tmp_path, monkeypatch, capsys):
    # an untrusted root: the shell check is skipped, the runner still exits 0.
    monkeypatch.setenv("HARNESS_TRUST_STORE", str(tmp_path / "empty.json"))
    root = _tree(tmp_path, '          - {type: shell, cmd: "true"}\n')
    report = scr.run_root(root)
    rows = [r for r in report["checks"] if r["rule_id"] == "STD-Q-RG1-R1"]
    assert rows and rows[0]["result"] == "skipped"
    rc = scr.main(["--root", str(root)])
    assert rc == 0


def test_runner_honesty_marks_judged(tmp_path, capsys):
    root = _tree(tmp_path, '          - "assert something a human reasons about"\n')
    rc = scr.main(["--root", str(root)])
    assert rc == 0
    assert "judged" in capsys.readouterr().out.lower()


def test_main_symlink_root_skipped_consistent_with_runner(tmp_path, trust, capsys):
    # The CLI entry must gate symlinked roots the same way mechanical_runner does:
    # is_trusted refuses a symlinked root [F6], so main() must NOT pre-resolve the
    # --root away (that would collapse the symlink and silently defeat the refusal).
    real = tmp_path / "real"
    real.mkdir()
    _tree(real, '          - {type: shell, cmd: "true"}\n')
    trust(real)  # the REAL dir is trusted
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    rc = scr.main(["--root", str(link), "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    rows = [r for r in report["checks"] if r["rule_id"] == "STD-Q-RG1-R1"]
    assert rows and rows[0]["result"] == "skipped", (
        "a symlinked --root must be refused (not silently resolved to its trusted target)")
