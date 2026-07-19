"""Tests for mechanical_runner — the unified grep + trust-gated shell runner.

Grep detectors always run (line-scan, no exec — safe). Shell detectors auto-fire
ONLY when the repo is TRUSTED (an explicit operator vet); base-verify is an
integrity signal, NOT an authorizer. Otherwise they are skipped (dropped to
grep-only) with an advisory finding — never executed, never blocking, never wired
into a hook. The runner always returns a list and never raises (advisory-first).
"""

import hashlib
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import mechanical_runner  # noqa: E402
import trust_store  # noqa: E402


def _grep_rule():
    return {"id": "R-GREP", "severity": "info", "scope": ["**/*.py"],
            "detector": {"type": "grep", "pattern": r"^\s*except\s*:", "flags": ""}}


def _shell_rule(cmd="echo hit", source_file="r.yaml"):
    return {"id": "R-SHELL", "severity": "info", "scope": ["**/*.py"],
            "detector": {"type": "shell", "cmd": cmd}, "source_file": source_file}


def _empty_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TRUST_STORE", str(tmp_path / "trust.json"))


def test_grep_detector_always_runs(tmp_path, monkeypatch):
    _empty_store(tmp_path, monkeypatch)   # untrusted — grep must still run
    (tmp_path / "a.py").write_text(
        "def f():\n    try:\n        g()\n    except:\n        pass\n", encoding="utf-8")
    findings = mechanical_runner.run([_grep_rule()], ["a.py"], root=tmp_path)
    assert any(f["rule_id"] == "R-GREP" for f in findings)


def test_shell_detector_untrusted_skipped(tmp_path, monkeypatch):
    _empty_store(tmp_path, monkeypatch)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(mechanical_runner, "_run_shell",
                        lambda *a, **k: called.append(1) or {"executed": True, "rc": 0,
                                                             "stdout": "hit", "stderr": ""})
    findings = mechanical_runner.run([_shell_rule()], ["a.py"], root=tmp_path)
    assert not called                                   # never executed
    assert any(f.get("skipped") for f in findings)


def test_shell_detector_trusted_fires(tmp_path, monkeypatch):
    _empty_store(tmp_path, monkeypatch)
    trust_store.add_trust(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    findings = mechanical_runner.run([_shell_rule("echo hit")], ["a.py"], root=tmp_path)
    assert any("hit" in (f.get("match") or "") for f in findings)


def test_clone_modified_base_drops_to_grep(tmp_path, monkeypatch):
    # [F4] a rule whose source bytes mismatch the manifest is NOT base-verified;
    # in an untrusted repo its shell detector is skipped even though the file is
    # under harness/.
    _empty_store(tmp_path, monkeypatch)
    rulefile = tmp_path / "harness" / "standards" / "areas" / "x.std.yaml"
    rulefile.parent.mkdir(parents=True)
    rulefile.write_text("orig", encoding="utf-8")
    (tmp_path / "harness" / "manifest.json").write_text(
        json.dumps({"files": {"harness/standards/areas/x.std.yaml":
                              hashlib.sha256(b"orig").hexdigest()}}), encoding="utf-8")
    rulefile.write_text("TAMPERED", encoding="utf-8")   # clone modified the base rule
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(mechanical_runner, "_run_shell",
                        lambda *a, **k: called.append(1) or {"executed": True, "rc": 0,
                                                             "stdout": "", "stderr": ""})
    rule = _shell_rule(source_file="harness/standards/areas/x.std.yaml")
    findings = mechanical_runner.run([rule], ["a.py"], root=tmp_path)
    assert not called
    assert any(f.get("skipped") for f in findings)


def test_base_verified_still_skipped_without_trust(tmp_path, monkeypatch):
    # base-verify is NO LONGER an authorizer: an intact base rule in an UNTRUSTED
    # repo is skipped, not fired. Trust is the only key (closes the
    # self-attesting-manifest hole — a hostile clone controls both bytes and the
    # in-tree manifest).
    _empty_store(tmp_path, monkeypatch)
    rulefile = tmp_path / "harness" / "standards" / "areas" / "x.std.yaml"
    rulefile.parent.mkdir(parents=True)
    rulefile.write_text("orig", encoding="utf-8")
    (tmp_path / "harness" / "manifest.json").write_text(
        json.dumps({"files": {"harness/standards/areas/x.std.yaml":
                              hashlib.sha256(b"orig").hexdigest()}}), encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(mechanical_runner, "_run_shell",
                        lambda *a, **k: called.append(1) or {"executed": True, "rc": 0,
                                                             "stdout": "", "stderr": ""})
    rule = _shell_rule("echo verified", source_file="harness/standards/areas/x.std.yaml")
    findings = mechanical_runner.run([rule], ["a.py"], root=tmp_path)
    assert not called, "base-verify must not authorize a shell detector"
    assert any(f.get("skipped") for f in findings)


def test_base_verify_not_an_authorizer_in_runner():
    # [F6] the auto-fire path no longer calls is_base_verified — base-verify is
    # integrity-only. The only key to a shell detector is repo trust.
    src = (_SCRIPTS / "mechanical_runner.py").read_text(encoding="utf-8")
    assert "is_base_verified" not in src, \
        "mechanical_runner must not use base-verify as an authorizer"


def test_runner_always_exit0_on_bad_detector(tmp_path, monkeypatch):
    _empty_store(tmp_path, monkeypatch)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    bad = {"id": "R", "severity": "info", "scope": ["**/*.py"],
           "detector": {"type": "grep", "pattern": "(", "flags": ""}}  # broken regex
    findings = mechanical_runner.run([bad], ["a.py"], root=tmp_path)   # must not raise
    assert isinstance(findings, list)


def test_runner_never_raises_on_bad_root():
    # a malformed root yields no findings rather than raising (never-raise)
    rule = _shell_rule()
    assert mechanical_runner.run([rule], ["a.py"], root=None) == []
    grep = _grep_rule()
    assert mechanical_runner.run([grep], ["a.py"], root=None) == []


def test_runner_not_in_blocking_hook():
    # [RT] no hook imports/runs the mechanical runner (the RCE boundary holds)
    hooks_dir = _SCRIPTS.parent / "hooks"
    for p in hooks_dir.glob("*.py"):
        assert "mechanical_runner" not in p.read_text(encoding="utf-8"), p
