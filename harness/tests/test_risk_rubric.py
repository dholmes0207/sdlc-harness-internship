"""test_risk_rubric.py — derive a risk tier (tiny/normal/high_risk) from the
files a change touches, and map the tier to a ceremony.

Hard-gate globs (auth/migration/secret/api-contract) force high_risk regardless
of flag count; otherwise the flag count picks the tier. Path classification for
the dependency-manifest flag is REUSED from change_class_derivation (no parallel
grep). `enabled: false` collapses everything to tiny (the rubric is a tunable,
off-switchable advisory, not a hard gate).
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import risk_rubric as rr  # noqa: E402
import change_class_derivation as ccd  # noqa: E402


# A deterministic policy so glob tuning of the shipped yaml does not move these.
_POLICY = {
    "enabled": True,
    "hard_gates": {
        "auth": {"globs": ["**/auth/**", "**/security/**"]},
        "migration": {"globs": ["**/migrations/**", "**/migration/**"]},
        "secret": {"globs": ["**/*secret*", "**/.env*"]},
        "api_contract": {"globs": ["**/api/**", "**/*.proto"]},
    },
    "flags": [
        {"name": "config", "globs": ["**/config/**", "**/*.config.*"]},
        {"name": "ci_infra", "globs": ["**/Dockerfile", "**/.github/**"]},
        {"name": "schema", "globs": ["**/schema/**", "**/*.graphql"]},
    ],
    "thresholds": {"tiny_max": 1, "normal_max": 3},
    "ceremony": {
        "tiny": {"require_plan": False, "require_security_scan": False, "require_non_author_review": False},
        "normal": {"require_plan": True, "require_security_scan": False, "require_non_author_review": False},
        "high_risk": {"require_plan": True, "require_security_scan": True, "require_non_author_review": True},
    },
}


def test_auth_touch_high_risk():
    r = rr.derive_risk(".", ["src/auth/login.py"], policy=_POLICY)
    assert r.tier == "high_risk"
    assert "auth" in r.gates_hit
    assert r.ceremony["require_security_scan"] is True


def test_widened_auth_surface_high_risk():
    # The shipped rubric covers the common auth/migration/route surface.
    pol = rr.load_policy()
    for f in ["src/login.py", "auth/jwt_utils.py", "core/session.go",
              "migrations/versions/abc123.py", "app/routes.py"]:
        assert rr.derive_risk(".", [f], policy=pol).tier == "high_risk", f


def test_auth_glob_case_insensitive():
    # Glob matching is case-insensitive — capitalized auth files (Java/TS
    # conventions) must still trip the shipped auth gate.
    pol = rr.load_policy()
    for f in ["pkg/Auth.java", "src/OAuth.ts", "service/AUTH/token.go"]:
        r = rr.derive_risk(".", [f], policy=pol)
        assert r.tier == "high_risk", f


def test_migration_high_risk():
    r = rr.derive_risk(".", ["db/migrations/003_drop_column.sql"], policy=_POLICY)
    assert r.tier == "high_risk"
    assert "migration" in r.gates_hit


def test_trivial_tiny():
    r = rr.derive_risk(".", ["src/util/strings.py"], policy=_POLICY)
    assert r.tier == "tiny"
    assert r.gates_hit == []
    assert r.flags == []


def test_flag_count_normal():
    # two flags, no hard gate → normal
    r = rr.derive_risk(".", ["package.json", "Dockerfile"], policy=_POLICY)
    assert r.tier == "normal"
    assert "dependency_manifest" in r.flags  # via change_class_derivation reuse
    assert "ci_infra" in r.flags


def test_flag_count_high_risk_without_hard_gate():
    # 4+ flags and NO hard gate must still reach high_risk (the count branch,
    # never exercised by the hard-gate tests).
    r = rr.derive_risk(".", ["config/app.yaml", "Dockerfile",
                             "db/schema/x.sql", "package.json"], policy=_POLICY)
    assert r.tier == "high_risk"
    assert r.gates_hit == []
    assert len(r.flags) >= 4


def test_threshold_boundaries():
    # exactly tiny_max (1) → tiny; exactly normal_max (3) → normal.
    one = rr.derive_risk(".", ["Dockerfile"], policy=_POLICY)
    assert one.tier == "tiny" and len(one.flags) == 1
    three = rr.derive_risk(".", ["Dockerfile", "config/app.yaml",
                                 "db/schema/x.sql"], policy=_POLICY)
    assert three.tier == "normal" and len(three.flags) == 3


def test_flag_count_normal_asserts_ceremony():
    r = rr.derive_risk(".", ["package.json", "Dockerfile"], policy=_POLICY)
    assert r.tier == "normal"
    assert r.ceremony["require_plan"] is True
    assert r.ceremony["require_security_scan"] is False


def test_non_string_glob_entry_raises():
    pol = dict(_POLICY, hard_gates={"auth": {"globs": [123]}})
    with pytest.raises(rr.RiskRubricError):
        rr.derive_risk(".", ["a.py"], policy=pol)


def test_disabled_no_elevation():
    pol = dict(_POLICY, enabled=False)
    r = rr.derive_risk(".", ["src/auth/login.py"], policy=pol)
    assert r.tier == "tiny"
    assert r.ceremony.get("require_security_scan", False) is False


def test_reuses_change_class_signals(monkeypatch):
    calls = {"n": 0}
    real = ccd._is_manifest

    def spy(p):
        calls["n"] += 1
        return real(p)

    monkeypatch.setattr(ccd, "_is_manifest", spy)
    rr.derive_risk(".", ["pyproject.toml"], policy=_POLICY)
    assert calls["n"] > 0  # manifest classification routed through change_class_derivation


def test_malformed_hard_gate_spec_raises():
    # a hard_gate whose globs is a bare string must fail LOUD, not iterate it
    # char-by-char (which would silently fail-open / downgrade to tiny).
    pol = dict(_POLICY, hard_gates={"auth": "**/auth/**"})  # string, not {globs:[...]}
    with pytest.raises(rr.RiskRubricError) as exc:
        rr.derive_risk(".", ["src/auth/x.py"], policy=pol)
    assert "auth" in str(exc.value)


def test_malformed_flag_entry_raises():
    pol = dict(_POLICY, flags=["not-a-mapping"])
    with pytest.raises(rr.RiskRubricError):
        rr.derive_risk(".", ["a.py"], policy=pol)


def test_shipped_rubric_loads_and_enabled():
    # the shipped risk-rubric.yaml parses and is enabled by default
    pol = rr.load_policy()
    assert pol.get("enabled") is True
    assert pol.get("hard_gates")
    r = rr.derive_risk(".", ["app/auth/token.py"], policy=pol)
    assert r.tier == "high_risk"
