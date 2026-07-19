"""The six authored persona bundles — the CONTENT contract.

Phase-1 shipped one stub to exercise the schema; the six real characters replace
it. These tests lock the count, that each is schema-valid, that forms map to the
real catalog, that voice levels are in range, that ids are pairwise-distinct AND
disjoint from the 24 reserved ids, and a soft mechanical sycophancy lint (a human
approved the content; this only guards a future edit from regressing candor).
"""
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_DATA = Path(__file__).resolve().parent.parent / "data"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import persona_bundle  # noqa: E402
import voice_prefs     # noqa: E402


def _bundles():
    return persona_bundle.load()  # the shipped registry


def _preset_ids():
    import yaml
    d = yaml.safe_load((_DATA / "voice-presets.yaml").read_text(encoding="utf-8"))
    return {p["id"] for p in d["presets"]}


def test_all_six_bundles_present():
    assert len(_bundles()) == 6


def test_each_bundle_passes_validate():
    for b in _bundles():
        persona_bundle.validate(b)  # schema + maxlen + form + level


def test_bundle_forms_in_catalog():
    forms = set(voice_prefs.WORK_PERSONAS + voice_prefs.FUN_PERSONAS)
    for b in _bundles():
        assert b["form"] in forms, "%s has an off-catalog form %r" % (b["id"], b["form"])


def test_default_voice_levels_in_range():
    for b in _bundles():
        assert 1 <= b["default_voice_level"] <= 9, b["id"]


def test_bundle_ids_parity():
    banned = set(voice_prefs.PERSONAS) | _preset_ids()
    for b in _bundles():
        assert b["id"] not in banned, "%s collides with a reserved id" % b["id"]


def test_six_bundle_ids_pairwise_distinct():
    # F11: assert on the RAW id list, not on valid_ids() — a set silently swallows a
    # duplicate, so two bundles sharing an id would pass a set-based check.
    ids = [b["id"] for b in _bundles()]
    assert len(ids) == 6
    assert len(ids) == len(set(ids)), "duplicate bundle id in the registry"


def test_no_sycophancy_motif():
    # Mechanical soft lint (candor-floor): a human approved the content; this only
    # catches a future edit reintroducing an obvious sycophancy motif.
    bad = re.compile(r"reassur|fragile|đừng buồn|động viên|mong manh", re.I)
    for b in _bundles():
        blob = "%s %s" % (b.get("soul", ""), b.get("characteristic", ""))
        assert not bad.search(blob), "%s carries a sycophancy motif" % b["id"]
