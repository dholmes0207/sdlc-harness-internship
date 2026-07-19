"""Tests for rule_audit_cache — the content-hash cache over the conflict audit.

The audit (user_override.detect_conflicts of layer-b new rules vs shipped std
rules) is re-run ONLY when a rule actually changes: a layer-b rule added/removed/
edited, a shipped rule's content_hash changed, or an override-source file changed
(folder file or legacy root). Otherwise a cache hit returns the stored conflicts
without re-auditing. The cache JSON lives under harness/state/ (gitignored).
"""

import sys
import pytest
from pathlib import Path

import yaml as _yaml

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import rule_audit_cache  # noqa: E402
import user_override  # noqa: E402


_SHIPPED = """id: STD-REVIEW-PY
type: std_area
zone: operational
title: "Python Review"
rule_groups:
  - id: STD-REVIEW-PY-RG1
    title: "PY"
    rules:
      - id: STD-REVIEW-PY-RG1-R1
        title: "py r1"
        scope: ["**/*.py"]
        severity: critical
"""


def _repo(tmp_path, overrides):
    """A minimal repo: one shipped operational rule + a layer-b override folder."""
    areas = tmp_path / "harness" / "standards" / "areas"
    areas.mkdir(parents=True, exist_ok=True)
    (areas / "STD-REVIEW-PY.std.yaml").write_text(_SHIPPED, encoding="utf-8")
    folder = tmp_path / "docs" / "standards"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "harness-self.std.yaml").write_text(
        _yaml.safe_dump({"overrides": overrides}), encoding="utf-8")
    return tmp_path


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "harness" / "state"))
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)


def _count_calls(monkeypatch):
    calls = {"n": 0}
    real = user_override.detect_conflicts

    def _wrapped(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(user_override, "detect_conflicts", _wrapped)
    return calls


# an opposite-severity (info vs the shipped critical) new rule on the same scope
_CONFLICT_OV = [{"rule_id": "USR-HARNESS-X", "reason": "demo",
                 "severity": "info", "scope": ["**/*.py"]}]


def test_first_call_audits_and_writes(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    _repo(tmp_path, _CONFLICT_OV)
    calls = _count_calls(monkeypatch)
    rec = rule_audit_cache.audit_or_cached(tmp_path)
    assert calls["n"] == 1                       # miss -> audited
    cache = tmp_path / "harness" / "state" / "rule-audit-cache.json"
    assert cache.is_file()
    assert rec["conflicts"]                       # the opposite-severity overlap


def test_unchanged_is_cache_hit(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    _repo(tmp_path, _CONFLICT_OV)
    calls = _count_calls(monkeypatch)
    rule_audit_cache.audit_or_cached(tmp_path)
    rule_audit_cache.audit_or_cached(tmp_path)
    assert calls["n"] == 1                        # second call hit the cache


def test_layerb_change_reaudits(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    _repo(tmp_path, _CONFLICT_OV)
    calls = _count_calls(monkeypatch)
    rule_audit_cache.audit_or_cached(tmp_path)
    # add a layer-b rule -> id-list + per-rule hashes change -> re-audit
    _repo(tmp_path, _CONFLICT_OV + [{"rule_id": "USR-HARNESS-Y", "reason": "d",
                                     "severity": "info", "scope": ["**/*.md"]}])
    rule_audit_cache.audit_or_cached(tmp_path)
    assert calls["n"] == 2


def test_shipped_change_reaudits(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    _repo(tmp_path, _CONFLICT_OV)
    calls = _count_calls(monkeypatch)
    rule_audit_cache.audit_or_cached(tmp_path)
    # flip the shipped rule's severity -> its content_hash changes -> re-audit
    areas = tmp_path / "harness" / "standards" / "areas"
    (areas / "STD-REVIEW-PY.std.yaml").write_text(
        _SHIPPED.replace("severity: critical", "severity: info"), encoding="utf-8")
    rule_audit_cache.audit_or_cached(tmp_path)
    assert calls["n"] == 2


def test_override_source_change_reaudits(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    _repo(tmp_path, _CONFLICT_OV)
    calls = _count_calls(monkeypatch)
    rule_audit_cache.audit_or_cached(tmp_path)
    # add a NEW override file whose merged overrides do not change the conflict
    # set, but the source set changed -> re-audit (F5)
    extra = tmp_path / "docs" / "standards" / "extra.std.yaml"
    extra.write_text(_yaml.safe_dump({"overrides": []}), encoding="utf-8")
    rule_audit_cache.audit_or_cached(tmp_path)
    assert calls["n"] == 2


def test_per_rule_hashes_identify_change(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    _repo(tmp_path, _CONFLICT_OV)
    rec1 = rule_audit_cache.audit_or_cached(tmp_path)
    # editing the shipped rule changes exactly its per-rule entry
    areas = tmp_path / "harness" / "standards" / "areas"
    (areas / "STD-REVIEW-PY.std.yaml").write_text(
        _SHIPPED.replace("severity: critical", "severity: info"), encoding="utf-8")
    rec2 = rule_audit_cache.audit_or_cached(tmp_path)
    changed = {k for k in rec1["per_rule"]
               if rec1["per_rule"].get(k) != rec2["per_rule"].get(k)}
    assert changed == {"STD-REVIEW-PY-RG1-R1"}


@pytest.mark.dev_repo
def test_cache_under_gitignored_state(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    _repo(tmp_path, _CONFLICT_OV)
    rule_audit_cache.audit_or_cached(tmp_path)
    cache = tmp_path / "harness" / "state" / "rule-audit-cache.json"
    assert cache.is_file()
    # the real repo gitignores harness/state/* so the cache never enters git
    gi = (Path(__file__).resolve().parent.parent.parent / ".gitignore").read_text()
    assert "harness/state/*" in gi
