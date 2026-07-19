"""hs:spec — HTML self-containment: sanitize-lib fail-closed, no CDN, ever
(XSS bug_class watch, visualization-spec.md:31).
"""

import sys
from pathlib import Path

from conftest import VALID  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_NAMES = [
    "encoding_utils", "frontmatter_parser", "id_grammar", "spec_graph",
    "check_traceability", "fs_guard", "i18n_labels", "render_common",
    "render_ascii_board", "render_ascii", "render_mermaid",
    "render_html_escape", "render_html_tooltip", "render_html_count_grid",
    "render_html_risk_grid", "render_html_competition",
    "render_html_governance", "render_html_assets", "render_html",
]
_mods = load_skill_scripts(_SPEC_SCRIPTS, _NAMES)
for _n in ("check_traceability", "render_ascii", "render_html_risk_grid",
           "render_html_competition", "i18n_labels"):
    sys.modules[_n] = _mods[_n]

spec_graph = _mods["spec_graph"]
render_html = _mods["render_html"]
render_html_escape = _mods["render_html_escape"]
render_html_assets = _mods["render_html_assets"]


def _graph():
    return spec_graph.build_graph(VALID)


# ── _escape chokepoint ────────────────────────────────────────────────────────

def test_escape_covers_lt_gt_amp_quote():
    raw = """<script>alert('x')</script> & "quoted\""""
    out = render_html_escape._escape(raw)
    assert "<" not in out and ">" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out
    assert "&quot;" in out
    assert "&#39;" in out


# ── HTML body fail-closed (no vendored marked/DOMPurify -> escaped text +
#    banner, never a CDN) ────────────────────────────────────────────────────

def test_body_render_fails_closed_when_markdown_libs_missing(monkeypatch):
    monkeypatch.setattr(render_html_assets, "VENDOR_MARKED", Path("/nonexistent/marked.min.js"))
    monkeypatch.setattr(render_html_assets, "VENDOR_PURIFY", Path("/nonexistent/purify.min.js"))
    values = render_html_assets.body_render_values({"x": 1})
    assert values["libs_banner"], "missing libs must show a visible fail-closed banner"
    # No CDN *script tag* / URL is ever injected for the markdown sanitizer —
    # the body-render path fails CLOSED to escaped text (same offline-only
    # invariant the Mermaid loader now also honors, see
    # test_mermaid_fails_closed_no_cdn_when_vendored_missing); the banner
    # PROSE is allowed to say the word "CDN" while explaining that none was used.
    assert "<script" not in values["markdown_libs"]
    assert "cdn.jsdelivr" not in values["markdown_libs"]
    assert "cdn.jsdelivr" not in values["libs_banner"]
    # The sanitize chokepoint's fallback (escaped <pre>) stays present even
    # when the libs are absent — psRenderMarkdown degrades, it is not removed.
    assert "psRenderMarkdown" in values["markdown_libs"]


def test_body_render_no_banner_when_libs_vendored():
    # The real vendored files ARE committed (assets/vendor/) — confirm the
    # happy path carries no fail-closed banner and no CDN string.
    values = render_html_assets.body_render_values({"x": 1})
    assert values["libs_banner"] == ""
    assert "cdn" not in values["markdown_libs"].lower()


def test_mermaid_fails_closed_no_cdn_when_vendored_missing(monkeypatch):
    # Offline-only invariant: when vendored mermaid.min.js is absent, the
    # page must degrade to the inert escaped <pre> diagram source — never
    # inject an external <script src=...> (a CDN fetch). Both bindings are
    # patched: render_html_assets.VENDOR_MERMAID (the module the vendored-JS
    # loader actually checks) and render_html's own re-exported copy (what
    # this file's _render_view_body / assemble read).
    missing = Path("/nonexistent/mermaid.min.js")
    monkeypatch.setattr(render_html_assets, "VENDOR_MERMAID", missing)
    monkeypatch.setattr(render_html, "VENDOR_MERMAID", missing)
    g = _graph()
    mermaid_text = '```mermaid\nflowchart BT\n  A["x"]\n```'
    html = render_html.assemble("tree", "mermaid", mermaid_text, g, lang="en")
    assert "cdn.jsdelivr" not in html
    assert "<script src=" not in html
    assert "<pre>" in html
    assert "flowchart BT" in html  # the diagram source is still shown, escaped


# ── risk view: HTML-native fragment also escapes spec-derived text ─────────

def test_risk_html_native_escapes_malicious_description():
    g = _graph()
    g["risks"] = [{
        "node": "PRD-AUTH-E1",
        "description": "<img src=x onerror=alert(1)>",
        "impact": "high", "likelihood": "high",
        "mitigation": "<b>none</b>", "status": "open",
    }]
    frag = render_html.risk(g)
    assert "<img" not in frag
    assert "&lt;img" in frag


# ── Unicode bidi-override (Trojan Source, CVE-2021-42574) ────────────────────

def test_escape_strips_unicode_bidi_override():
    # The HTML escaper is a separate copy from render_common, so it must ALSO
    # strip the bidi-control block — else an RLO/PDF pair rides through the
    # escaped count/risk/competition tables and visually reverses rendered text
    # in a browser (HTML-entity escaping does not neutralize bidi format chars).
    out = render_html_escape._escape("Auth\u202eEVIL\u202c")
    assert "\u202e" not in out and "\u202c" not in out
    assert "Auth" in out and "EVIL" in out


def test_tip_scalar_strips_unicode_bidi_override():
    # _tip_scalar feeds the hover-tooltip title via textContent, which blocks
    # markup injection but NOT Unicode bidi controls — strip them here too.
    out = render_html_escape._tip_scalar("Auth\u202eEVIL\u202c")
    assert "\u202e" not in out and "\u202c" not in out


# ---------------------------------------------------------------------------
# render_common promises _CONTROL_RE is stripped at EVERY render chokepoint,
# but the board/explorer JSON island (embed_spec_data) serialized card fields
# + bodies raw — a bidi override (U+202E RLO, Trojan-Source CVE-2021-42574) in
# a title/status landed unstripped and reordered text via textContent. The
# island serializer must strip control/bidi before emitting.
# ---------------------------------------------------------------------------

def test_embed_spec_data_strips_bidi_override():
    payload = {"nodes": [{"id": "PRD-X", "title": "safe\u202eEVIL\u2069",
                          "body": "line\u202eHIDDEN\u2069 end"}]}
    out = render_html_assets.embed_spec_data(payload)
    assert "\u202e" not in out and "\u2069" not in out
    assert "EVIL" in out and "safe" in out and "HIDDEN" in out


def test_embed_spec_data_strips_c0_control():
    payload = {"nodes": [{"id": "PRD-X", "title": "ok\x1b[2J\x07 done"}]}
    out = render_html_assets.embed_spec_data(payload)
    assert "\x1b" not in out and "\x07" not in out
    assert "done" in out


def test_write_visual_lone_surrogate_does_not_crash(tmp_path):
    # A lone UTF-16 surrogate (e.g. a hand-edited `title: "\uD800bad"`) rides the
    # node into the emitted HTML and used to crash the UTF-8 write sink with
    # UnicodeEncodeError, breaking the always-exit-0 contract every other reader
    # honors. The sink must scrub it (U+FFFD) like the JSON island already does.
    (tmp_path / "docs" / "product").mkdir(parents=True)
    html = "<html><title>\ud800bad</title>ok</html>"
    target = render_html._write_visual(tmp_path, "tree-x.html", html)
    assert target.is_file()
    written = target.read_text(encoding="utf-8")
    assert "\ud800" not in written
    assert "�" in written and "ok" in written
