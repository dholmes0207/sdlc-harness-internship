"""Red-green TDD for the .drawio id-targeted edit engine (edit_drawio.py).

The engine re-implements next-ai-draw-io's applyDiagramOperations (update/add/
delete by cell_id) in stdlib + defusedxml: fail-soft, deterministic, layout-
preserving. These tests pin the contract red-team RT-B1..B4 / RT-S1..S3 forced.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import xml.etree.ElementTree as STD_ET

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "plugins/hs/skills/drawio/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import edit_drawio  # noqa: E402

EDIT_SCRIPT = SCRIPTS_DIR / "edit_drawio.py"
VALIDATE_SCRIPT = SCRIPTS_DIR / "validate.py"


# --- Fixtures --------------------------------------------------------------

SINGLE = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="drawio">
  <diagram name="Page-1">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="2" value="A" style="rounded=1;" vertex="1" parent="1">
          <mxGeometry x="100" y="100" width="120" height="60" as="geometry" />
        </mxCell>
        <mxCell id="3" value="B" style="rounded=0;" vertex="1" parent="1">
          <mxGeometry x="300" y="100" width="120" height="60" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""

# Two pages; cell 5 lives on page 2.
MULTIPAGE = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="drawio">
  <diagram name="Page-1">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="2" value="A" vertex="1" parent="1">
          <mxGeometry x="100" y="100" width="120" height="60" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
  <diagram name="Page-2">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="5" value="E" vertex="1" parent="1">
          <mxGeometry x="200" y="200" width="80" height="40" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""

# Container 2 holds nested child 4 (parent=2, not root's 1).
NESTED = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="drawio">
  <diagram name="Page-1">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="2" value="Box" style="container=1;" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="400" height="300" as="geometry" />
        </mxCell>
        <mxCell id="4" value="Child" vertex="1" parent="2">
          <mxGeometry x="20" y="20" width="100" height="40" as="geometry" />
        </mxCell>
        <mxCell id="6" value="Sibling" vertex="1" parent="2">
          <mxGeometry x="20" y="120" width="100" height="40" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""

# A page whose body is compressed (no <root> element under mxGraphModel).
NO_ROOT = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="drawio">
  <diagram name="Page-1">jVLBToQ==</diagram>
</mxfile>
"""

ENTITY_FILE = """<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>
<mxfile><diagram><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="2" value="&lol2;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="10" height="10"/></mxCell>
</root></mxGraphModel></diagram></mxfile>
"""


def _cell(xml, cid):
    """Return the mxCell Element with the given id from a serialized tree, or None."""
    root = STD_ET.fromstring(xml)
    for c in root.iter("mxCell"):
        if c.get("id") == cid:
            return c
    return None


def _errs(res):
    return [e for e in res.errors if e.startswith("error")]


def _warns(res):
    return [e for e in res.errors if e.startswith("warning")]


# --- Core (next-ai parity) -------------------------------------------------

def test_update_replaces_targeted_cell():
    ops = [{"operation": "update", "cell_id": "2",
            "new_xml": '<mxCell id="2" value="A2" vertex="1" parent="1">'
                       '<mxGeometry x="100" y="100" width="120" height="60" as="geometry"/></mxCell>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert not _errs(res)
    assert _cell(res.result, "2").get("value") == "A2"


def test_update_id_mismatch_is_error():
    ops = [{"operation": "update", "cell_id": "2",
            "new_xml": '<mxCell id="999" value="X" vertex="1" parent="1">'
                       '<mxGeometry x="0" y="0" width="10" height="10"/></mxCell>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert _errs(res)
    assert _cell(res.result, "2").get("value") == "A"  # untouched


def test_add_new_cell_appends():
    ops = [{"operation": "add", "cell_id": "9",
            "new_xml": '<mxCell id="9" value="N" vertex="1" parent="1">'
                       '<mxGeometry x="500" y="100" width="80" height="40"/></mxCell>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert not _errs(res)
    assert _cell(res.result, "9") is not None


def test_add_existing_id_is_error():
    ops = [{"operation": "add", "cell_id": "2",
            "new_xml": '<mxCell id="2" value="dup" vertex="1" parent="1">'
                       '<mxGeometry x="0" y="0" width="10" height="10"/></mxCell>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert _errs(res)
    assert len([c for c in STD_ET.fromstring(res.result).iter("mxCell")
                if c.get("id") == "2"]) == 1


def test_delete_removes_cell():
    ops = [{"operation": "delete", "cell_id": "2"}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert not _errs(res)
    assert _cell(res.result, "2") is None


def test_delete_missing_is_error():
    ops = [{"operation": "delete", "cell_id": "404"}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert _errs(res)


# --- Geometry (VL-1 / RT-S1) ----------------------------------------------

def test_update_missing_geometry_preserves():
    ops = [{"operation": "update", "cell_id": "2",
            "new_xml": '<mxCell id="2" value="A2" vertex="1" parent="1"/>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert not _errs(res)
    assert _warns(res)
    g = _cell(res.result, "2").find("mxGeometry")
    assert g is not None and g.get("x") == "100" and g.get("y") == "100"


def test_update_empty_geometry_preserves():
    ops = [{"operation": "update", "cell_id": "2",
            "new_xml": '<mxCell id="2" value="A2" vertex="1" parent="1">'
                       '<mxGeometry as="geometry"/></mxCell>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert not _errs(res)
    assert _warns(res)
    g = _cell(res.result, "2").find("mxGeometry")
    assert g.get("x") == "100" and g.get("y") == "100"


def test_update_faithful_drops_geometry():
    ops = [{"operation": "update", "cell_id": "2",
            "new_xml": '<mxCell id="2" value="A2" vertex="1" parent="1"/>'}]
    res = edit_drawio.apply_operations(SINGLE, ops, faithful=True)
    assert not _errs(res)
    assert not _warns(res)
    assert _cell(res.result, "2").find("mxGeometry") is None


def test_untargeted_geometry_unchanged():
    ops = [{"operation": "update", "cell_id": "2",
            "new_xml": '<mxCell id="2" value="A2" vertex="1" parent="1">'
                       '<mxGeometry x="100" y="100" width="120" height="60"/></mxCell>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    g = _cell(res.result, "3").find("mxGeometry")
    assert g.get("x") == "300" and g.get("width") == "120"


# --- Reserved + structure (RT-B4 / RT-B1) ---------------------------------

def test_delete_reserved_refused():
    for rid in ("0", "1"):
        res = edit_drawio.apply_operations(SINGLE, [{"operation": "delete", "cell_id": rid}])
        assert _errs(res)
        assert _cell(res.result, rid) is not None


def test_update_reserved_refused():
    ops = [{"operation": "update", "cell_id": "1",
            "new_xml": '<mxCell id="1" value="hijack" vertex="1" parent="0"/>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert _errs(res)
    c1 = _cell(res.result, "1")
    assert c1 is not None and c1.get("value") is None


def test_add_reserved_refused():
    ops = [{"operation": "add", "cell_id": "0",
            "new_xml": '<mxCell id="0" value="x" vertex="1" parent="1"/>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert _errs(res)


def test_update_nested_cell():
    ops = [{"operation": "update", "cell_id": "4",
            "new_xml": '<mxCell id="4" value="Child2" vertex="1" parent="2">'
                       '<mxGeometry x="20" y="20" width="100" height="40"/></mxCell>'}]
    res = edit_drawio.apply_operations(NESTED, ops)
    assert not _errs(res)
    assert _cell(res.result, "4").get("value") == "Child2"
    # Container and sibling intact.
    assert _cell(res.result, "2").get("value") == "Box"
    assert _cell(res.result, "6").get("value") == "Sibling"
    assert _cell(res.result, "4").get("parent") == "2"


def test_multipage_targets_correct_page():
    ops = [{"operation": "update", "cell_id": "5",
            "new_xml": '<mxCell id="5" value="E2" vertex="1" parent="1">'
                       '<mxGeometry x="200" y="200" width="80" height="40"/></mxCell>'}]
    res = edit_drawio.apply_operations(MULTIPAGE, ops)
    assert not _errs(res)
    assert _cell(res.result, "5").get("value") == "E2"
    assert _cell(res.result, "2").get("value") == "A"  # page-1 untouched
    # cell 5 still under page-2's root (a diagram named Page-2 contains it)
    root = STD_ET.fromstring(res.result)
    page2 = [d for d in root.findall("diagram") if d.get("name") == "Page-2"][0]
    assert any(c.get("id") == "5" for c in page2.iter("mxCell"))


def test_add_missing_parent_warns():
    ops = [{"operation": "add", "cell_id": "9",
            "new_xml": '<mxCell id="9" value="orphan" vertex="1">'
                       '<mxGeometry x="0" y="0" width="10" height="10"/></mxCell>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert not _errs(res)
    assert _warns(res)
    assert _cell(res.result, "9") is not None


# --- Fail-soft + safety (RT-S3, AC#7/8) -----------------------------------

def test_failsoft_continues_after_bad_op():
    ops = [
        {"operation": "delete", "cell_id": "404"},          # bad
        {"operation": "add", "cell_id": "9",
         "new_xml": '<mxCell id="9" value="N" vertex="1" parent="1">'
                    '<mxGeometry x="0" y="0" width="10" height="10"/></mxCell>'},  # good
    ]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert len(_errs(res)) == 1
    assert _cell(res.result, "9") is not None


def test_malformed_fragment_is_error():
    ops = [{"operation": "update", "cell_id": "2", "new_xml": "<mxCell"}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert _errs(res)
    assert _cell(res.result, "2").get("value") == "A"


def test_entity_attack_blocked():
    # Entity in the FILE itself must not expand and must not crash.
    res = edit_drawio.apply_operations(ENTITY_FILE, [{"operation": "delete", "cell_id": "2"}])
    assert _errs(res)
    assert "lollollollol" not in res.result
    # Entity in a fragment must also be refused.
    payload = ('<!DOCTYPE x [<!ENTITY a "boom">]>'
               '<mxCell id="2" value="&a;" vertex="1" parent="1"/>')
    res2 = edit_drawio.apply_operations(SINGLE, [{"operation": "update", "cell_id": "2", "new_xml": payload}])
    assert _errs(res2)
    assert "boom" not in res2.result


def test_no_root_page_no_crash():
    res = edit_drawio.apply_operations(NO_ROOT, [{"operation": "delete", "cell_id": "2"}])
    assert _errs(res)  # nothing to locate
    assert isinstance(res.result, str)  # never threw


# --- Output + determinism + semantic-preserve (RT-B2) ---------------------

def test_deterministic_repeat():
    ops = [{"operation": "update", "cell_id": "2",
            "new_xml": '<mxCell id="2" value="A2" vertex="1" parent="1">'
                       '<mxGeometry x="100" y="100" width="120" height="60"/></mxCell>'}]
    a = edit_drawio.apply_operations(SINGLE, ops).result
    b = edit_drawio.apply_operations(SINGLE, ops).result
    assert a == b


def test_untargeted_cells_semantically_identical():
    ops = [{"operation": "update", "cell_id": "2",
            "new_xml": '<mxCell id="2" value="A2" vertex="1" parent="1">'
                       '<mxGeometry x="100" y="100" width="120" height="60"/></mxCell>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    before, after = _cell(SINGLE, "3"), _cell(res.result, "3")
    assert before.tag == after.tag
    assert before.attrib == after.attrib
    gb, ga = before.find("mxGeometry"), after.find("mxGeometry")
    assert gb.attrib == ga.attrib


def test_output_has_xml_declaration_and_validates():
    ops = [{"operation": "update", "cell_id": "2",
            "new_xml": '<mxCell id="2" value="A2" vertex="1" parent="1">'
                       '<mxGeometry x="100" y="100" width="120" height="60"/></mxCell>'}]
    res = edit_drawio.apply_operations(SINGLE, ops)
    assert res.result.lstrip().startswith("<?xml")
    with tempfile.NamedTemporaryFile(suffix=".drawio", mode="w", delete=False) as f:
        f.write(res.result)
        tmp = f.name
    try:
        out = subprocess.run([sys.executable, str(VALIDATE_SCRIPT), tmp],
                             capture_output=True, timeout=15)
        assert out.returncode == 0, out.stdout.decode() + out.stderr.decode()
    finally:
        Path(tmp).unlink(missing_ok=True)


# --- CLI -------------------------------------------------------------------

def test_list_cells_json_shape():
    with tempfile.NamedTemporaryFile(suffix=".drawio", mode="w", delete=False) as f:
        f.write(MULTIPAGE)
        tmp = f.name
    try:
        out = subprocess.run([sys.executable, str(EDIT_SCRIPT), tmp, "--list-cells"],
                             capture_output=True, timeout=15)
        assert out.returncode == 0, out.stderr.decode()
        cells = json.loads(out.stdout.decode())
        ids = {c["id"] for c in cells}
        assert "2" in ids and "5" in ids
        assert "0" not in ids and "1" not in ids
        c2 = [c for c in cells if c["id"] == "2"][0]
        assert c2["label"] == "A" and c2["x"] == 100 and "page" in c2
        c5 = [c for c in cells if c["id"] == "5"][0]
        assert c5["page"] == 2
    finally:
        Path(tmp).unlink(missing_ok=True)


def test_exit_code_on_op_error():
    with tempfile.NamedTemporaryFile(suffix=".drawio", mode="w", delete=False) as f:
        f.write(SINGLE)
        tmp = f.name
    bad_ops = json.dumps([{"operation": "delete", "cell_id": "404"}])
    good_ops = json.dumps([{"operation": "delete", "cell_id": "2"}])
    try:
        r_bad = subprocess.run([sys.executable, str(EDIT_SCRIPT), tmp, "--ops", "-"],
                               input=bad_ops.encode(), capture_output=True, timeout=15)
        assert r_bad.returncode == 1
        r_good = subprocess.run([sys.executable, str(EDIT_SCRIPT), tmp, "--ops", "-"],
                                input=good_ops.encode(), capture_output=True, timeout=15)
        assert r_good.returncode == 0
    finally:
        Path(tmp).unlink(missing_ok=True)
