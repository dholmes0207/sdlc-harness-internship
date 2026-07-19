"""test_review_policy_config.py — read/set the shared review-policy.yaml.

review-policy.yaml is the new shared config that names tactical review profiles
(rounds + the four axes), a per-hard-stage effort/rounds floor (ships OFF), and
caps. The loader is fail-open non-breaking: an ABSENT file resolves to a default
dict with every stage floor disabled, so a fresh install behaves exactly as
before. The writer is surgical and block-scoped (nested keys), preserving the
header and every untouched line.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import review_policy_config as rp  # noqa: E402

_SAMPLE = """# review-policy.yaml — keep this header
# Absent file => all stage floors OFF (non-breaking).

profiles:
  default:
    rounds: 1
    compounding: false
    per_aspect: false
    blind_main_sub: false
    refute: false
    effort: low
    scope: diff
    aspects: [correctness]
  thorough:
    rounds: 3
    compounding: true
    per_aspect: true
    blind_main_sub: false
    refute: true
    effort: high
    scope: diff
    aspects: [security, dry, correctness]
  ship-grade:
    rounds: 3
    compounding: true
    per_aspect: true
    blind_main_sub: true
    refute: true
    effort: max
    scope: project
    aspects: [security, dry, correctness]

stage_floor:
  pr:
    enabled: false
    min_effort: low
    min_rounds: 1
  merge:
    enabled: false
    min_effort: low
    min_rounds: 1
  ship:
    enabled: false
    min_effort: low
    min_rounds: 1
  deploy:
    enabled: false
    min_effort: low
    min_rounds: 1

caps:
  max_rounds: 5
  max_lenses_per_round: 8
"""


def _write(tmp_path, text=_SAMPLE):
    p = tmp_path / "review-policy.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_absent_file_defaults_all_floors_off(tmp_path):
    policy = rp.load_review_policy(path=tmp_path / "nope.yaml")
    floors = policy["stage_floor"]
    assert set(floors) >= {"pr", "merge", "ship", "deploy"}
    assert all(floors[s]["enabled"] is False for s in ("pr", "merge", "ship", "deploy"))


def test_absent_file_has_three_profiles_and_caps(tmp_path):
    policy = rp.load_review_policy(path=tmp_path / "nope.yaml")
    assert set(policy["profiles"]) >= {"default", "thorough", "ship-grade"}
    assert policy["caps"]["max_rounds"] == 5
    assert policy["caps"]["max_lenses_per_round"] == 8


def test_roundtrip_stage_floor_nondefault(tmp_path):
    p = _write(tmp_path)
    rp.save_review_policy(
        {"stage_floor.ship.enabled": "true",
         "stage_floor.ship.min_effort": "high",
         "stage_floor.ship.min_rounds": "3"},
        path=p)
    floor = rp.load_review_policy(path=p)["stage_floor"]["ship"]
    assert floor["enabled"] is True
    assert floor["min_effort"] == "high"
    assert floor["min_rounds"] == 3


def test_roundtrip_profile_knob_nondefault(tmp_path):
    p = _write(tmp_path)
    rp.save_review_policy({"profiles.default.rounds": "4"}, path=p)
    assert rp.load_review_policy(path=p)["profiles"]["default"]["rounds"] == 4


def test_unknown_key_rejected_before_write(tmp_path):
    p = _write(tmp_path)
    before = p.read_text(encoding="utf-8")
    with pytest.raises(rp.ReviewPolicyConfigError):
        rp.save_review_policy({"bogus.x": "1"}, path=p)
    assert p.read_text(encoding="utf-8") == before  # file untouched


def test_bad_effort_enum_raises(tmp_path):
    bad = _SAMPLE.replace("effort: low", "effort: ultra", 1)
    p = _write(tmp_path, bad)
    with pytest.raises(rp.ReviewPolicyConfigError):
        rp.load_review_policy(path=p)


def test_resolve_profile_unknown_falls_back_default(tmp_path):
    policy = rp.load_review_policy(path=_write(tmp_path))
    prof = rp.resolve_profile("does-not-exist", policy)
    assert prof == policy["profiles"]["default"]


def test_resolve_profile_known(tmp_path):
    policy = rp.load_review_policy(path=_write(tmp_path))
    assert rp.resolve_profile("ship-grade", policy)["effort"] == "max"


def test_surgical_preserves_comments(tmp_path):
    p = _write(tmp_path)
    before = p.read_text(encoding="utf-8").splitlines()
    rp.save_review_policy({"stage_floor.ship.enabled": "true"}, path=p)
    after = p.read_text(encoding="utf-8").splitlines()
    assert "# review-policy.yaml — keep this header" in after
    # exactly one line changed
    diff = [i for i in range(min(len(before), len(after))) if before[i] != after[i]]
    assert len(diff) == 1
    assert len(before) == len(after)


def test_non_mapping_document_raises(tmp_path):
    p = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(rp.ReviewPolicyConfigError):
        rp.load_review_policy(path=p)


def test_partial_profiles_keep_builtin_defaults(tmp_path):
    """A config declaring only a custom profile must NOT drop the built-in
    default/thorough profiles (per-block fill, matching orchestration_config)."""
    import review_policy_config as rp
    p = tmp_path / "review-policy.yaml"
    p.write_text("profiles:\n  custom:\n    rounds: 2\n", encoding="utf-8")
    policy = rp.load_review_policy(str(p))
    assert "default" in policy["profiles"], "built-in default profile lost"
    assert "thorough" in policy["profiles"], "built-in thorough profile lost"
    assert policy["profiles"]["custom"]["rounds"] == 2
    assert rp.resolve_profile("default", policy)["rounds"] == 1
