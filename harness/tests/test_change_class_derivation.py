"""test_change_class_derivation.py — derive a change-class from MULTIPLE fact
signals, never from a self-declared label.

The contract under test (the security spine):
  - derive() returns (cls, signals, ambiguous) — a signal-SET plus an
    `ambiguous` flag, NOT a fabricated confidence-%; enforcement downstream
    keys off signal agreement, not a self-computed float.
  - a commit-message type ("feat:", "fix:") is NEVER a signal — passing one in
    must not move the verdict.
  - src-only-no-test → refactor, ambiguous=True (soft-only; no differentiator).
  - src + an EMPTY test (no assertions) does not lift to a hard feature.
  - an explicit override (env / git trailer) wins but is traced as
    change_class_override (actor + ts) so the bypass is git-visible.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import change_class_derivation as ccd  # noqa: E402


# --------------------------------------------------------------- core signals --
def test_src_plus_real_test_is_feature_hard():
    d = ccd.derive(
        ["src/checkout.py", "tests/test_checkout.py"],
        manifest_changed=False, tests_added=True, tests_have_assertions=True)
    assert d.cls == "feature"
    assert d.ambiguous is False
    assert "src_changed" in d.signals and "test_has_assertions" in d.signals


def test_src_only_no_test_is_refactor_ambiguous():
    d = ccd.derive(["src/checkout.py"], tests_added=False)
    assert d.cls == "refactor"
    assert d.ambiguous is True  # soft-only, no hard differentiator


def test_empty_test_does_not_lift_to_feature_hard():
    # a test file was added but carries no assertions → not enough for a hard
    # feature gate; stays ambiguous so enforcement degrades to soft.
    d = ccd.derive(
        ["src/checkout.py", "tests/test_checkout.py"],
        tests_added=True, tests_have_assertions=False)
    assert d.ambiguous is True


def test_regression_test_plus_src_is_bugfix():
    d = ccd.derive(
        ["src/auth.py", "tests/test_auth_regression.py"],
        tests_added=True, tests_have_assertions=True, regression_tests=True)
    assert d.cls == "bugfix"
    assert "regression_test" in d.signals


def test_manifest_version_change_is_dep_bump():
    d = ccd.derive(
        ["pyproject.toml"], manifest_changed=True, manifest_version_changed=True)
    assert d.cls == "dep_bump_major"
    assert "manifest_version_changed" in d.signals


def test_version_bump_tag_no_code_is_release():
    d = ccd.derive(
        ["pyproject.toml"], manifest_changed=True, version_bump=True,
        tag_exists=True, net_code_change=False)
    assert d.cls == "release"


# ----------------------------------------------------- commit-label is no signal --
def test_commit_message_type_is_not_a_signal():
    # a conventional-commit "feat:" subject must NOT turn a refactor into a
    # feature — the label is 0% trusted.
    base = ccd.derive(["src/checkout.py"], tests_added=False)
    labeled = ccd.derive(["src/checkout.py"], tests_added=False,
                         commit_message="feat: add new checkout flow")
    assert labeled.cls == base.cls == "refactor"


# ------------------------------------------------------ override is traced -------
def test_env_override_wins_and_is_traced(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_CHANGE_CLASS", "release")
    d = ccd.derive(["src/checkout.py"], tests_added=False)
    assert d.cls == "release"
    assert "override" in d.signals
    # the bypass left an audit line
    traces = list((tmp_path / "trace").glob("trace-*.jsonl"))
    assert traces, "override must emit a change_class_override trace"
    events = [json.loads(ln) for ln in traces[0].read_text().splitlines()]
    assert any(e.get("event") == "change_class_override" for e in events)


def test_java_test_naming_detected():
    # Java conventions: *Test/*Tests (Surefire) + *IT/*ITCase (Failsafe), even
    # outside a /test/ path segment.
    assert ccd._is_test_path("com/onemount/ProductServiceTest.java")
    assert ccd._is_test_path("com/onemount/OrderTests.java")
    assert ccd._is_test_path("com/onemount/CheckoutIT.java")
    assert not ccd._is_test_path("com/onemount/ProductService.java")


def test_mockito_verify_counts_as_assertion(tmp_path):
    # A command-service test that asserts only via Mockito verify() (no
    # assertThat) must still count as a real assertion.
    (tmp_path / "FooTest.java").write_text(
        "class FooTest { void t(){ verify(repo).save(x); } }",
        encoding="utf-8")
    assert ccd._added_tests_assert(str(tmp_path), ["FooTest.java"])


def test_unpacks_as_tuple():
    cls, signals, ambiguous = ccd.derive(["src/x.py"], tests_added=False)
    assert cls == "refactor" and isinstance(signals, list) and ambiguous is True
