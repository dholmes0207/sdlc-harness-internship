"""make_preview_html.py TDD tests.

Tests verify offline preview HTML generation: file creation, XML embedding,
escaping, degrade behavior, and vendor presence.
"""
import hashlib
import pytest
import html as html_mod
import json
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAWIO_SKILL = REPO_ROOT / "harness" / "plugins" / "hs" / "skills" / "drawio"
SCRIPTS_DIR = DRAWIO_SKILL / "scripts"
VENDOR_DIR = DRAWIO_SKILL / "vendor"
MAKE_PREVIEW = SCRIPTS_DIR / "make_preview_html.py"

MINIMAL_DRAWIO = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <mxfile host="drawio" version="26.0.0">
      <diagram name="Page-1">
        <mxGraphModel>
          <root>
            <mxCell id="0" />
            <mxCell id="1" parent="0" />
            <mxCell id="2" value="Hello &amp; World" style="rounded=1;" vertex="1" parent="1">
              <mxGeometry x="100" y="100" width="120" height="60" as="geometry" />
            </mxCell>
          </root>
        </mxGraphModel>
      </diagram>
    </mxfile>
""")



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _write_drawio(tmp_dir: Path, content: str = MINIMAL_DRAWIO) -> Path:
    p = tmp_dir / "test.drawio"
    p.write_text(content)
    return p


def _run_preview(src: Path, extra_args=None, cwd=None) -> "tuple[int, str, str]":
    cmd = [sys.executable, str(MAKE_PREVIEW), str(src)]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=10,
        cwd=str(cwd or DRAWIO_SKILL),
    )
    return result.returncode, result.stdout.decode(), result.stderr.decode()


def test_make_preview_html_creates_file():
    """Running make_preview_html.py on a .drawio input creates an .html file."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = _write_drawio(tmp)
        rc, _, stderr = _run_preview(src)
        assert rc == 0, f"make_preview_html exit {rc}\nstderr: {stderr}"
        out_html = tmp / "test.html"
        assert out_html.exists(), "Expected test.html to be created"
        content = out_html.read_text()
        assert len(content) > 100, "HTML output too short"


def test_preview_embeds_xml_no_network():
    """HTML must embed XML and reference viewer.min.js via relative path, not CDN."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = _write_drawio(tmp)
        rc, _, stderr = _run_preview(src)
        assert rc == 0, f"exit {rc}: {stderr}"
        html_text = (tmp / "test.html").read_text()

        assert "mxgraph" in html_text, "Expected mxgraph div in HTML"

        # Script src tag for the viewer must be a relative path (not http/https)
        script_srcs = re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', html_text)
        for src_val in script_srcs:
            if "viewer" in src_val.lower() or "mxgraph" in src_val.lower():
                assert not src_val.startswith("http://") and not src_val.startswith("https://"), (
                    f"Viewer script src must be relative, not CDN: {src_val!r}"
                )


def test_preview_xml_escaped():
    """XML content embedded in the HTML data attribute must be parseable."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = _write_drawio(tmp, MINIMAL_DRAWIO)
        rc, _, _ = _run_preview(src)
        assert rc == 0
        html_text = (tmp / "test.html").read_text()

        # The data-mxgraph attribute holds HTML-escaped JSON; unescape then parse
        m = re.search(r'data-mxgraph="([^"]*)"', html_text)
        assert m, "data-mxgraph attribute not found"
        raw_attr = m.group(1)
        json_str = html_mod.unescape(raw_attr)
        data = json.loads(json_str)
        xml_str = data.get("xml", "")
        assert "mxCell" in xml_str, "XML should be embedded in data-mxgraph JSON"
        assert "mxfile" in xml_str, "mxfile root should be in embedded XML"


def test_degrade_when_vendor_missing():
    """Script must exit 0 even if vendor/viewer.min.js is missing."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = _write_drawio(tmp)
        rc, _, stderr = _run_preview(src, ["--vendor-dir", str(tmp / "no-vendor")])
        assert rc == 0, f"Should degrade gracefully, exit {rc}\nstderr: {stderr}"
        out_html = tmp / "test.html"
        assert out_html.exists(), "HTML must be created even without vendor"
        html_text = out_html.read_text()
        assert len(html_text) > 50, "Degraded HTML must have some content"


def test_vendor_viewer_present_and_licensed():
    """vendor/viewer.min.js must exist, be non-trivially sized, and have a license sidecar."""
    viewer = VENDOR_DIR / "viewer.min.js"
    assert viewer.exists(), (
        f"vendor/viewer.min.js not found at {viewer}. "
        "Must be fetched from draw.io jgraph/drawio (Apache-2.0)."
    )
    size = viewer.stat().st_size
    assert size > 100_000, f"viewer.min.js is too small ({size} bytes), expected ~0.5-2MB"

    license_file = VENDOR_DIR / "VIEWER-LICENSE.txt"
    assert license_file.exists(), "vendor/VIEWER-LICENSE.txt must document Apache-2.0 attribution"
    license_text = license_file.read_text()
    assert "Apache" in license_text or "apache" in license_text.lower(), (
        "VIEWER-LICENSE.txt must mention Apache license"
    )


def test_vendor_viewer_matches_recorded_provenance_hash():
    """The vendored viewer.min.js must match the SHA-256 recorded in its license
    sidecar — the provenance pin that catches a swapped/tampered binary."""
    viewer = VENDOR_DIR / "viewer.min.js"
    license_text = (VENDOR_DIR / "VIEWER-LICENSE.txt").read_text()
    m = re.search(r"SHA-256:\s*([0-9a-fA-F]{64})", license_text)
    assert m, "VIEWER-LICENSE.txt must record a SHA-256: <hex> provenance pin"
    recorded = m.group(1).lower()
    actual = hashlib.sha256(viewer.read_bytes()).hexdigest()
    assert actual == recorded, (
        f"viewer.min.js SHA-256 {actual} != recorded provenance {recorded} "
        "(binary swapped or license sidecar out of date)"
    )
