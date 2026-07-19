"""Structural shape test for every STD-REVIEW-* area file.

Covers acceptance criteria from the ECC language std-rules port plan:
- Every area parses as valid YAML
- Every area has zone: operational
- Every area id matches ^STD-[A-Z][A-Z0-9-]{0,15}$
- No duplicate ids across all areas
- Every scope is a list; no scope string contains { } ,
- No field contains the literal TBD in status/title/owner
- thin_source: true areas may have only 1 rule (others ≥2)
- Every detector is null or valid shaped
- Every rule has at least one identifiable anti-pattern (NEVER)
"""

import re
from pathlib import Path

import pytest
import yaml

AREAS_DIR = Path(__file__).resolve().parents[2] / "harness" / "standards" / "areas"
STD_AREA_ID = re.compile(r"^STD-[A-Z][A-Z0-9-]{0,15}$")
SCOPE_BRACE = re.compile(r"[\{\},]")


def _load_area(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _yield_areas():
    """Yield (path, area_dict) for every STD-REVIEW-*.std.yaml file."""
    for p in sorted(AREAS_DIR.glob("STD-REVIEW-*.std.yaml")):
        yield p, _load_area(p)


def _yield_area_rules(area: dict):
    """Yield (rule_group_id, rule_dict) for every rule in an area."""
    for rg in area.get("rule_groups") or []:
        rg_id = rg.get("id", "?")
        for rule in rg.get("rules") or []:
            yield rg_id, rule


@pytest.fixture(scope="session")
def all_areas():
    return list(_yield_areas())


# ── ID & zone ──────────────────────────────────────────────────────────────


def test_every_area_has_valid_id_and_zone(all_areas):
    for path, area in all_areas:
        aid = area.get("id", "")
        assert STD_AREA_ID.match(aid), (
            f"{path.name}: id {aid!r} does not match {STD_AREA_ID.pattern}"
        )
        assert area.get("zone") == "operational", (
            f"{path.name}: zone must be 'operational', got {area.get('zone')!r}"
        )


# ── No duplicate ids across all areas ──────────────────────────────────────


def test_no_dup_ids(all_areas):
    seen = {}
    for path, area in all_areas:
        aid = area["id"]
        if aid in seen:
            pytest.fail(f"Duplicate area id {aid!r} in {path.name} (also {seen[aid]})")
        seen[aid] = path.name
        for rg in area.get("rule_groups") or []:
            rg_id = rg.get("id", "?")
            if rg_id in seen:
                pytest.fail(f"Duplicate rule_group id {rg_id!r} in {path.name} (also {seen[rg_id]})")
            seen[rg_id] = f"{path.name}/RG"
            for rule in rg.get("rules") or []:
                rid = rule.get("id", "?")
                if rid in seen:
                    pytest.fail(f"Duplicate rule id {rid!r} in {path.name} (also {seen[rid]})")
                seen[rid] = f"{path.name}/{rg_id}"


# ── Scope checks ───────────────────────────────────────────────────────────


def test_scope_is_list_without_brace_comma(all_areas):
    """Every rule scope must be a list of single-extension globs; no { } , in any scope string."""
    for path, area in all_areas:
        for rg_id, rule in _yield_area_rules(area):
            scope = rule.get("scope")
            assert isinstance(scope, list), (
                f"{path.name}/{rg_id}/{rule.get('id','?')}: scope must be a list, got {type(scope).__name__}"
            )
            for s in scope:
                assert isinstance(s, str), (
                    f"{path.name}/{rg_id}/{rule.get('id','?')}: scope entry must be str, got {type(s).__name__}"
                )
                assert not SCOPE_BRACE.search(s), (
                    f"{path.name}/{rg_id}/{rule.get('id','?')}: scope {s!r} contains brace or comma"
                )


# ── No TBD ─────────────────────────────────────────────────────────────────


def test_no_tbd_in_required_fields(all_areas):
    """status, title, owner must not contain TBD."""
    for path, area in all_areas:
        for field in ("status", "title", "owner"):
            val = area.get(field, "")
            assert "TBD" not in str(val), (
                f"{path.name}: area.{field} contains TBD: {val!r}"
            )
        for rg in area.get("rule_groups") or []:
            for field in ("status", "title", "owner"):
                val = rg.get(field, "")
                assert "TBD" not in str(val), (
                    f"{path.name}/{rg.get('id','?')}: rule_group.{field} contains TBD: {val!r}"
                )
            for rule in rg.get("rules") or []:
                for field in ("status", "title", "owner"):
                    val = rule.get(field, "")
                    assert "TBD" not in str(val), (
                        f"{path.name}/{rg.get('id','?')}/{rule.get('id','?')}: "
                        f"rule.{field} contains TBD: {val!r}"
                    )


# ── Rule count (thin_source exception) ─────────────────────────────────────


def test_minimum_rule_count(all_areas):
    """Every area must have ≥1 rule_group and (≥2 rules OR thin_source:true + ≥1 rule)."""
    for path, area in all_areas:
        thin = area.get("thin_source", False)
        groups = area.get("rule_groups") or []
        assert len(groups) >= 1, f"{path.name}: must have ≥1 rule_group"
        total_rules = sum(len(rg.get("rules") or []) for rg in groups)
        if thin:
            assert total_rules >= 1, (
                f"{path.name}: thin_source area must have ≥1 rule, got {total_rules}"
            )
        else:
            assert total_rules >= 2, (
                f"{path.name}: non-thin_source area must have ≥2 rules, got {total_rules}"
            )


# ── Detector shape ─────────────────────────────────────────────────────────


def test_detector_is_null_or_valid(all_areas):
    """Detector must be null or a dict with type+pattern."""
    for path, area in all_areas:
        for rg_id, rule in _yield_area_rules(area):
            det = rule.get("detector")
            if det is None:
                continue
            assert isinstance(det, dict), (
                f"{path.name}/{rg_id}/{rule.get('id','?')}: detector must be null or dict"
            )
            assert "type" in det and "pattern" in det, (
                f"{path.name}/{rg_id}/{rule.get('id','?')}: detector must have type+pattern"
            )
            assert isinstance(det["type"], str), (
                f"{path.name}/{rg_id}/{rule.get('id','?')}: detector.type must be str"
            )
            assert isinstance(det["pattern"], str), (
                f"{path.name}/{rg_id}/{rule.get('id','?')}: detector.pattern must be str"
            )


# ── Rule quality: each rule must contain ALWAYS or NEVER (anti-pattern) ────


def test_every_rule_has_anti_pattern_in_description(all_areas):
    """Every rule description must contain ALWAYS or NEVER (identifiable anti-pattern).
    This enforces the C2 quality floor."""
    for path, area in all_areas:
        for rg_id, rule in _yield_area_rules(area):
            desc = rule.get("description") or ""
            assert ("ALWAYS" in desc or "NEVER" in desc or "PREFER" in desc), (
                f"{path.name}/{rg_id}/{rule.get('id','?')}: description must contain "
                f"ALWAYS/NEVER/PREFER (anti-pattern). Got: {desc[:80]}..."
            )


# ── Severity is closed set ─────────────────────────────────────────────────


def test_severity_is_valid(all_areas):
    for path, area in all_areas:
        for rg_id, rule in _yield_area_rules(area):
            sev = rule.get("severity", "")
            assert sev in ("critical", "info"), (
                f"{path.name}/{rg_id}/{rule.get('id','?')}: severity must be critical or info, "
                f"got {sev!r}"
            )
