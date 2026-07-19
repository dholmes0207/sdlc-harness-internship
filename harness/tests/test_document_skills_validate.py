"""test_document_skills_validate.py — the ported OOXML validator actually validates.

The harness reimplemented document-skills as prose-only and dropped the OOXML
schema validator (validate.py + the validation package + the XSD schema tree).
This drives the real validator engine against the real shipped schemas: a
well-formed, schema-valid OOXML part passes; a malformed part and a
schema-violating part are both flagged. Red before the port (the package + schemas
were absent). lxml is the validator's own runtime dep — skip (not fail) a host that
lacks it so a fresh install does not redden, but it runs for real where present.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

lxml_etree = pytest.importorskip("lxml.etree", reason="validator runtime dep lxml absent")

_REPO = Path(__file__).resolve().parents[2]
_DS = _REPO / "harness" / "plugins" / "hs" / "skills" / "document-skills"
_OOXML_SCRIPTS = _DS / "ooxml" / "scripts"
_VALIDATE_PY = _OOXML_SCRIPTS / "validate.py"
_SCHEMAS = _DS / "ooxml" / "schemas"

# Minimal, schema-VALID OPC relationships part (validates against opc-relationships.xsd).
_VALID_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="ppt/presentation.xml"/>'
    "</Relationships>"
)

# Same part with an element the schema does not allow at that position → schema-INVALID.
_SCHEMA_INVALID_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    "<NotARelationship/>"
    "</Relationships>"
)

# Not well-formed at all (unclosed tag).
_MALFORMED = '<?xml version="1.0"?>\n<Relationships><Relationship>'



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _load_validation_pkg():
    """Import the ported validation package from its on-disk scripts dir."""
    if str(_OOXML_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_OOXML_SCRIPTS))
    import validation  # noqa: WPS433 — load-on-demand from the ported tree

    return validation


def test_validator_assets_present():
    assert _VALIDATE_PY.exists(), "validate.py missing"
    assert (_OOXML_SCRIPTS / "validation" / "base.py").exists(), "validation package missing"
    assert _SCHEMAS.is_dir() and list(_SCHEMAS.rglob("*.xsd")), "no XSD schemas shipped"
    # The schema-path math in base.py resolves to ../../schemas relative to the package.
    resolved = (_OOXML_SCRIPTS / "validation").parent.parent / "schemas"
    assert resolved.resolve() == _SCHEMAS.resolve(), "schema dir not where base.py looks"


def test_schema_machinery_loads_and_accepts_valid_part(tmp_path):
    """A well-formed, schema-valid .rels validates clean against the shipped XSD."""
    pkg = _load_validation_pkg()
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    (unpacked / ".rels").write_text(_VALID_RELS, encoding="utf-8")

    v = pkg.PPTXSchemaValidator(unpacked, tmp_path / "orig.pptx", verbose=False)
    rels = unpacked / ".rels"
    is_valid, errors = v._validate_single_file_xsd(rels, unpacked)
    assert is_valid is True, f"valid .rels was rejected: {errors}"
    assert not errors


def test_schema_machinery_rejects_schema_violation(tmp_path):
    """A well-formed but schema-illegal part is flagged by the XSD layer."""
    pkg = _load_validation_pkg()
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    (unpacked / ".rels").write_text(_SCHEMA_INVALID_RELS, encoding="utf-8")

    v = pkg.PPTXSchemaValidator(unpacked, tmp_path / "orig.pptx", verbose=False)
    is_valid, errors = v._validate_single_file_xsd(unpacked / ".rels", unpacked)
    assert is_valid is False, "schema-illegal element slipped through"
    assert errors


def test_validate_xml_catches_malformed(tmp_path):
    """The well-formedness gate rejects a corrupted (unparseable) XML part."""
    pkg = _load_validation_pkg()
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    (unpacked / "broken.xml").write_text(_MALFORMED, encoding="utf-8")

    v = pkg.PPTXSchemaValidator(unpacked, tmp_path / "orig.pptx", verbose=False)
    assert v.validate_xml() is False, "malformed XML was not flagged"


def test_validate_xml_accepts_wellformed(tmp_path):
    pkg = _load_validation_pkg()
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    (unpacked / "ok.xml").write_text(_VALID_RELS, encoding="utf-8")

    v = pkg.PPTXSchemaValidator(unpacked, tmp_path / "orig.pptx", verbose=False)
    assert v.validate_xml() is True


def test_validate_file_references_flags_broken_target(tmp_path):
    """A .rels Target pointing at a missing file is reported as a broken reference."""
    pkg = _load_validation_pkg()
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    broken = _VALID_RELS.replace("ppt/presentation.xml", "ppt/does-not-exist.xml")
    (unpacked / ".rels").write_text(broken, encoding="utf-8")

    v = pkg.PPTXSchemaValidator(unpacked, tmp_path / "orig.pptx", verbose=False)
    # Re-collect xml files now that the .rels exists on disk.
    v.xml_files = [p for pat in ("*.xml", "*.rels") for p in unpacked.rglob(pat)]
    assert v.validate_file_references() is False, "broken .rels target not flagged"


def test_validate_cli_entrypoint_imports():
    """validate.py loads its package cleanly (catches import/syntax breakage in the port)."""
    proc = subprocess.run(
        [sys.executable, str(_VALIDATE_PY), "--help"],
        capture_output=True,
        timeout=30,
        cwd=str(_OOXML_SCRIPTS),
    )
    assert proc.returncode == 0, f"validate.py --help failed: {proc.stderr.decode()[:400]}"
    assert b"--original" in proc.stdout, "CLI did not advertise --original"


def test_no_upstream_brand_in_validator():
    src = (_OOXML_SCRIPTS / "validation" / "base.py").read_text(encoding="utf-8")
    low = (src + _VALIDATE_PY.read_text(encoding="utf-8")).lower()
    assert "claudekit" not in low and ".claude/" not in low, "upstream brand survived"
