"""persona_bundle registry: read-path tolerance, validate/maxlen raises, parity.

The read path (load/resolve/valid_ids) is tolerant and never raises; the
validate path does. Parity keeps the bundle id space disjoint from the persona +
preset id spaces, and keeps the module's literal form set in sync with the
voice_prefs catalog (the module stays a leaf — it never imports voice_prefs;
this test is the only place the two homes are compared).
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import persona_bundle  # noqa: E402
import voice_prefs  # noqa: E402  (TEST-only parity read)

_ENV = "HARNESS_PERSONA_BUNDLES"


def _write_registry(tmp_path, bundles):
    import yaml
    p = tmp_path / "persona-bundles.yaml"
    p.write_text(yaml.safe_dump({"bundles": bundles}, allow_unicode=True), encoding="utf-8")
    return p


def _valid_bundle(**over):
    b = {
        "id": "stub-example",
        "name": "Stub",
        "characteristic": "an example bundle used to exercise the schema",
        "soul": "exists only to prove the schema round-trips; carries no character",
        "form": "reality-check",
        "default_voice_level": 5,
    }
    b.update(over)
    return b


def _preset_ids():
    import yaml
    presets_path = Path(__file__).resolve().parent.parent / "data" / "voice-presets.yaml"
    d = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
    return {p["id"] for p in d["presets"]}


# --- read path: never-raise ---

def test_load_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(_ENV, str(tmp_path / "nope.yaml"))
    assert persona_bundle.load() == []


def test_load_malformed_yaml_returns_empty(tmp_path, monkeypatch):
    p = tmp_path / "persona-bundles.yaml"
    p.write_text("::: not valid: yaml: [", encoding="utf-8")
    monkeypatch.setenv(_ENV, str(p))
    assert persona_bundle.load() == []


def test_load_missing_bundles_key_returns_empty(tmp_path, monkeypatch):
    p = tmp_path / "persona-bundles.yaml"
    p.write_text("other: 1\n", encoding="utf-8")
    monkeypatch.setenv(_ENV, str(p))
    assert persona_bundle.load() == []


def test_resolve_known_stub(tmp_path, monkeypatch):
    _write_registry(tmp_path, [_valid_bundle()])
    monkeypatch.setenv(_ENV, str(tmp_path / "persona-bundles.yaml"))
    b = persona_bundle.resolve("stub-example")
    assert b is not None
    for f in ("id", "name", "characteristic", "soul", "form", "default_voice_level"):
        assert f in b


def test_resolve_unknown_returns_none(tmp_path, monkeypatch):
    _write_registry(tmp_path, [_valid_bundle()])
    monkeypatch.setenv(_ENV, str(tmp_path / "persona-bundles.yaml"))
    assert persona_bundle.resolve("nope") is None
    assert persona_bundle.resolve(None) is None


# --- validate/write path: raises ---

def test_validate_maxlen_soul_over_limit_raises():
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.validate(_valid_bundle(soul="x" * 801))


def test_validate_maxlen_name_char_over_limit_raises():
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.validate(_valid_bundle(name="x" * 41))
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.validate(_valid_bundle(characteristic="x" * 301))


def test_validate_form_must_be_known_persona():
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.validate(_valid_bundle(form="not-a-persona"))
    # 'none' is the standalone-persona default, never a valid bundle FORM.
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.validate(_valid_bundle(form="none"))


def test_validate_missing_field_raises():
    b = _valid_bundle()
    del b["soul"]
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.validate(b)


def test_validate_default_voice_level_range():
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.validate(_valid_bundle(default_voice_level=0))
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.validate(_valid_bundle(default_voice_level=10))
    # a bool must not satisfy the int level (True == 1 in Python)
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.validate(_valid_bundle(default_voice_level=True))


def test_stub_bundle_passes_validate():
    persona_bundle.validate(_valid_bundle())  # clean, no raise


def test_check_maxlen_helper_raises_over_limit():
    persona_bundle.check_maxlen("f", "x" * 10, 10)  # exactly at limit: ok
    with pytest.raises(persona_bundle.PersonaBundleError):
        persona_bundle.check_maxlen("f", "x" * 11, 10)


# --- shipped registry ---

def test_all_shipped_bundles_valid():
    bundles = persona_bundle.load()  # shipped default registry
    assert bundles, "shipped persona-bundles.yaml must have at least one bundle"
    for b in bundles:
        persona_bundle.validate(b)


def test_parity_bundle_ids_disjoint():
    banned = set(voice_prefs.PERSONAS) | _preset_ids()
    for b in persona_bundle.load():
        assert b["id"] not in banned, (
            f"bundle id {b['id']!r} collides with a persona/preset id (24 banned)")


def test_valid_ids_matches_registry(tmp_path, monkeypatch):
    _write_registry(tmp_path, [_valid_bundle(id="a"), _valid_bundle(id="b")])
    monkeypatch.setenv(_ENV, str(tmp_path / "persona-bundles.yaml"))
    assert persona_bundle.valid_ids() == {"a", "b"}


def test_form_catalog_in_sync():
    # module holds the valid-form set as a literal (leaf, no voice_prefs import);
    # this is the ONLY place the two homes are compared, so drift fails here.
    assert persona_bundle.VALID_FORMS == set(
        voice_prefs.WORK_PERSONAS + voice_prefs.FUN_PERSONAS)
