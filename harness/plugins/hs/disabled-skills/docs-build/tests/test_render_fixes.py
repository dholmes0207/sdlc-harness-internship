"""Unit coverage for FIX-21 (de-link dead cross-doc links) + FIX-22 (inline portable
png as base64). SSOT-side tests: pure functions, no docs/ tree needed — the consumer
proves them end-to-end via e2e, this guards them in the harness source.
"""
import base64
import types

import render_md_page as rmp  # noqa: E402  (conftest puts scripts/ on sys.path)
import ssg_engine as engine    # noqa: E402


# --- FIX-21: _delink_dead ---

def test_delink_strips_md_link_keeps_text():
    html = '<p>see <a href="../overview/platform.md">Platform</a> doc</p>'
    assert rmp._delink_dead(html) == "<p>see Platform doc</p>"


def test_delink_strips_yaml_yml_json():
    for ext in ("modules.yaml", "x.yml", "data.json"):
        out = rmp._delink_dead('<a href="../%s">L</a>' % ext)
        assert out == "L", ext


def test_delink_keeps_http_and_anchor_in_path():
    keep = '<a href="https://example.com/x.md">ext</a>'
    assert rmp._delink_dead(keep) == keep
    # extension detected even with #fragment / ?query
    assert rmp._delink_dead('<a href="a.md#h">L</a>') == "L"
    assert rmp._delink_dead('<a href="a.md?v=1">L</a>') == "L"


def test_delink_leaves_html_and_png_links_alone():
    keep = '<a href="pages/modules.html">page</a>'
    assert rmp._delink_dead(keep) == keep


# --- FIX-22: _inline_imgs ---

def _ctx(diagram_dir):
    return types.SimpleNamespace(diagram_dir=diagram_dir)


def test_inline_none_dir_is_noop():
    html = '<img src="../diagram/png/x.png">'
    assert engine._inline_imgs(_ctx(None), html) == html


def test_inline_rewrites_png_to_base64(tmp_path):
    png_bytes = b"\x89PNG\r\n\x1a\nFAKE"
    (tmp_path / "C1.png").write_bytes(png_bytes)
    html = '<img src="../diagram/png/C1.png" alt="c1">'
    out = engine._inline_imgs(_ctx(tmp_path), html)
    expect_b64 = base64.b64encode(png_bytes).decode("ascii")
    assert "data:image/png;base64,%s" % expect_b64 in out
    assert "../diagram/png/C1.png" not in out


def test_inline_missing_file_left_alone(tmp_path):
    html = '<img src="../diagram/png/missing.png">'
    assert engine._inline_imgs(_ctx(tmp_path), html) == html


def test_inline_non_diagram_img_untouched(tmp_path):
    html = '<img src="assets/logo.svg">'
    assert engine._inline_imgs(_ctx(tmp_path), html) == html
