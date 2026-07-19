"""hs:spec — visualize.py + render family: view catalog, ascii/mermaid
determinism, per-view default format, CLI error handling, port-hygiene gates.
"""

import io
import contextlib
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from conftest import VALID  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# Literal path keeps the stashed-skill collect_ignore coupling working:
# harness/plugins/hs/skills/spec/scripts
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
_SPEC_REFERENCES = _SPEC_SCRIPTS.parent / "references"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_NAMES = [
    "encoding_utils", "frontmatter_parser", "id_grammar", "spec_graph",
    "check_traceability", "check_fence", "fs_guard", "i18n_labels", "render_common",
    "render_ascii_board", "render_ascii", "render_mermaid",
    "render_html_escape", "render_html_tooltip", "render_html_count_grid",
    "render_html_risk_grid", "render_html_competition",
    "render_html_governance", "render_html_assets", "render_html",
    "render_board", "render_explorer", "visuals_retention", "snapshot",
    "visualize",
]
_mods = load_skill_scripts(_SPEC_SCRIPTS, _NAMES)
# A handful of these files do a LAZY (function-body) `import <sibling>`
# instead of a module-top-level one (render_ascii._counts_footer imports
# check_traceability; render_html.write imports visuals_retention; etc.) so
# they resolve at CALL time, after load_skill_scripts has already restored
# sys.path. None of these names collide with a harness/scripts module (unlike
# encoding_utils/frontmatter_parser/spec_graph/fs_guard, which DO collide and
# must stay under the loader's save/restore discipline) — pinning them into
# sys.modules for this test module's lifetime is what makes those lazy
# imports resolve the way they do in a real `python3 visualize.py` invocation
# (where the script's own directory stays on sys.path for the process).
for _n in ("check_traceability", "render_ascii", "render_mermaid",
           "visuals_retention", "render_html_risk_grid",
           "render_html_competition", "i18n_labels"):
    sys.modules[_n] = _mods[_n]

spec_graph = _mods["spec_graph"]
render_ascii = _mods["render_ascii"]
render_mermaid = _mods["render_mermaid"]
render_html = _mods["render_html"]
render_html_governance = _mods["render_html_governance"]
i18n_labels = _mods["i18n_labels"]
check_fence = _mods["check_fence"]
visualize = _mods["visualize"]
snapshot = _mods["snapshot"]
render_board = _mods["render_board"]
render_explorer = _mods["render_explorer"]
visuals_retention = _mods["visuals_retention"]
render_common = _mods["render_common"]


def _graph():
    return spec_graph.build_graph(VALID)


def _run_cli(argv):
    """Invoke visualize.main() in-process, capturing argv/stdout/stderr."""
    old_argv = sys.argv
    sys.argv = ["visualize.py"] + argv
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = visualize.main()
        return rc, out.getvalue(), err.getvalue()
    finally:
        sys.argv = old_argv


def _tmp_project():
    tmp = Path(tempfile.mkdtemp())
    proj = tmp / "proj"
    shutil.copytree(VALID, proj)
    return proj


# ── View catalog (visualization-spec.md:13-31) ───────────────────────────────

def test_view_catalog_matches_visualization_spec():
    # 14 graph/body views, matching visualization-spec.md:13-31 (the `audit`
    # governance view was never shipped — dropped from the catalog rather
    # than advertised-but-broken).
    expected = {
        "tree", "heatmap", "scope", "roadmap", "persona", "gap", "moscow",
        "risk", "competition", "time", "dashboard", "delta", "board",
        "explorer",
    }
    assert set(visualize.VIEWS) == expected
    assert len(visualize.VIEWS) == 14


# ── ASCII determinism (exact text) ───────────────────────────────────────────

_TREE_ASCII = (
    "PRODUCT: Acme Shop\n"
    "  [goal:BRD-G1] Reach $1M ARR in 12 months · approved\n"
    "    [prd:PRD-AUTH] Auth PRD · approved\n"
    "      [epic:PRD-AUTH-E1] Sign-In Epic · draft\n"
    "        [story:PRD-AUTH-E1-S1] Sign-In Story · draft\n"
    "  [goal:BRD-G2] Hit 80% repeat-purchase rate · approved\n"
    "— 5 nodes · 2 goals · 1 prd · 1 epic · 1 story · 1 findings"
)

_ROADMAP_ASCII = (
    "## NOW\n"
    "  - PRD-AUTH\n"
    "  - PRD-AUTH-E1\n"
    "  - PRD-AUTH-E1-S1\n"
    "## NEXT\n"
    "  (empty)\n"
    "## LATER\n"
    "  (empty)"
)


def test_tree_ascii_exact_text_and_deterministic():
    g = _graph()
    a = render_ascii.tree(g, lang="en", filter_wont=False)
    b = render_ascii.tree(g, lang="en", filter_wont=False)
    assert a == b  # same input -> byte-identical run-to-run
    assert a == _TREE_ASCII


def _graph_with_dangling_epic_parent():
    # Repoint the epic's parent edge at a PRD that does not exist -> the epic (and
    # its story) become unreachable from the goal-rooted tree walk, though both
    # stay real, visible nodes the footer still counts.
    g = _graph()
    for e in g["edges"]:
        if e["from"] == "PRD-AUTH-E1" and e["to"] == "PRD-AUTH":
            e["to"] = "PRD-GONE"
    return g


def test_tree_ascii_flags_nodes_hidden_by_broken_ancestor_chain():
    g = _graph_with_dangling_epic_parent()
    text = render_ascii.tree(g, lang="en", filter_wont=False)
    # A goal-rooted walk cannot reach the epic/story once a middle parent ref
    # dangles, so they drop from the body -- but the footer still counts them.
    assert "[epic:PRD-AUTH-E1]" not in text
    assert "[story:PRD-AUTH-E1-S1]" not in text
    assert "1 epic" in text and "1 story" in text
    # The body must NOT silently contradict the footer: an explicit hidden-count
    # line reconciles the two and points the PO at the gap view.
    assert "hidden" in text
    assert "2 node" in text  # epic + story


def test_tree_ascii_no_hidden_note_on_clean_graph():
    # Every visible node is reachable -> no discrepancy, no note (regression guard
    # so the note never fires on a well-formed spec).
    text = render_ascii.tree(_graph(), lang="en", filter_wont=False)
    assert "hidden" not in text


def test_roadmap_ascii_now_next_later_grouping():
    g = _graph()
    text = render_ascii.roadmap(g, lang="en", filter_wont=False)
    assert text == _ROADMAP_ASCII


# ── Mermaid v11 ───────────────────────────────────────────────────────────────

def test_safe_id_no_collision_between_distinct_raw_ids():
    # `-` -> `__` in the char-substitution encoding, so a well-formed
    # "PRD-FOO" and a malformed-but-still-graph-present "PRD__FOO" (a literal
    # double underscore, which the id grammar rejects but a renderer never
    # refuses to draw) used to collapse onto the SAME Mermaid node id and
    # silently merge two unrelated nodes in the rendered graph.
    a = render_mermaid._safe_id("PRD-FOO")
    b = render_mermaid._safe_id("PRD__FOO")
    assert a != b


def test_gantt_malformed_target_date_does_not_leak_into_dateformat_line():
    g = _graph()
    for n in g["nodes"]:
        if n["id"] == "PRD-AUTH":
            n["target_date"] = "2026-08-01"
        if n["id"] == "PRD-AUTH-E1":
            n["target_date"] = "Sept 2026"  # non-ISO, breaks `dateFormat YYYY-MM-DD`
    text = render_mermaid.time(g, lang="en", filter_wont=False)
    assert "Sept 2026" not in text
    assert "dateFormat YYYY-MM-DD" in text
    assert "2026-08-01" in text


def test_tree_mermaid_fenced_flowchart_bt():
    g = _graph()
    text = render_mermaid.tree(g, lang="en", filter_wont=False)
    assert text.startswith("```mermaid\n")
    assert text.endswith("```")
    assert "flowchart BT" in text
    # Deterministic: same graph -> byte-identical mermaid text.
    assert text == render_mermaid.tree(g, lang="en", filter_wont=False)


# ── Default format per-view (risk/competition/dashboard + board/explorer =
#    html; everything else = ascii) ──────────────────────────────────────────

def test_default_format_risk_is_html_native():
    proj = _tmp_project()
    rc, out, err = _run_cli(["--root", str(proj), "--view", "risk"])
    assert rc == 0
    written = proj / "docs" / "product" / "visuals"
    assert list(written.glob("risk-*.html")), "risk defaulted to html but no file was written"


def test_default_format_tree_is_ascii():
    proj = _tmp_project()
    rc, out, err = _run_cli(["--root", str(proj), "--view", "tree"])
    assert rc == 0
    assert out.strip() == _TREE_ASCII
    written = proj / "docs" / "product" / "visuals"
    assert not written.exists() or not list(written.glob("tree-*.html"))


# ── CLI error handling ────────────────────────────────────────────────────────

def test_empty_after_layers_exits_nonzero():
    proj = _tmp_project()
    rc, out, err = _run_cli(
        ["--root", str(proj), "--view", "board", "--layers", "nonexistent"]
    )
    assert rc != 0
    assert "unknown value" in err.lower() or "--layers" in err


def test_delta_no_baseline_message_not_a_crash():
    proj = _tmp_project()
    rc, out, err = _run_cli(["--root", str(proj), "--view", "delta"])
    assert rc == 0
    assert "no baseline" in out.lower()


def test_ascii_delta_non_dict_baseline_product_does_not_crash():
    # A hand-edited/malformed --snapshot baseline can carry a `product` that
    # is not a mapping (e.g. `"product": "MyApp"`) — the ASCII delta's own
    # per-field PRODUCT diff formatting must degrade, not raise.
    current = _graph()
    baseline = dict(current)
    baseline["product"] = "MyApp"
    text = render_ascii.delta(current, baseline)
    assert isinstance(text, str)


def test_mermaid_delta_non_dict_baseline_product_does_not_crash():
    current = _graph()
    baseline = dict(current)
    baseline["product"] = "MyApp"
    text = render_mermaid.delta(current, baseline)
    assert isinstance(text, str)


def test_load_baseline_rejects_non_dict_json(tmp_path):
    # A syntactically-valid but non-object JSON baseline (array/string/number from a
    # hand-edit or bad merge) passes json.loads but has no .get()/["id"]. It must
    # degrade to the same clean ValueError as corrupt JSON, not crash downstream in
    # diff_graphs with an uncaught AttributeError.
    snap = tmp_path / "docs" / "product" / "visuals" / ".snapshots"
    snap.mkdir(parents=True)
    (snap / "base.json").write_text('["not","a","dict"]', encoding="utf-8")
    with pytest.raises(ValueError):
        visualize._load_baseline(tmp_path, "base.json")


def test_delta_non_dict_baseline_is_clean_error_not_traceback():
    # End-to-end: a non-dict .snapshots/*.json must exit clean (stderr message,
    # non-zero rc) through the existing _load_baseline error funnel — never a raw
    # AttributeError traceback out of diff_graphs.
    proj = _tmp_project()
    snap = proj / "docs" / "product" / "visuals" / ".snapshots"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "base.json").write_text('["not","a","dict"]', encoding="utf-8")
    rc, out, err = _run_cli(["--root", str(proj), "--view", "delta",
                             "--format", "ascii", "--snapshot", "base.json"])
    assert rc != 0
    assert "Traceback" not in err and "AttributeError" not in err
    assert "snapshot" in err.lower()


def test_board_payload_title_routes_through_scalar_guard():
    # A malformed `title:` (a list from a hand-edit) must not leak a raw
    # array into the client JSON — every sibling field (status/moscow/
    # horizon) already routes through _scalar(); title must too.
    graph, artifacts = spec_graph.build_graph_with_artifacts(VALID)
    for n in graph["nodes"]:
        if n["id"] == "PRD-AUTH":
            n["title"] = ["not", "a", "string"]
    payload = render_board.build_payload(graph, artifacts)
    card = next(c for c in payload["cards"] if c["id"] == "PRD-AUTH")
    assert card["title"] == ""


def test_explorer_payload_title_and_owner_route_through_scalar_guard():
    graph, artifacts = spec_graph.build_graph_with_artifacts(VALID)
    for n in graph["nodes"]:
        if n["id"] == "PRD-AUTH":
            n["title"] = ["not", "a", "string"]
            n["owner"] = {"nested": "dict"}
    payload = render_explorer.build_payload(graph, artifacts)
    item = next(i for i in payload["items"] if i["id"] == "PRD-AUTH")
    assert item["title"] == ""
    assert item["owner"] == ""


def test_make_snapshot_missing_spec_root_raises_clean_error(tmp_path):
    # --snapshot invoked before any spec exists yet (docs/product/ absent) —
    # shutil.copytree on a missing source used to raise a bare, uncaught
    # FileNotFoundError. Must raise the dedicated, catchable error instead.
    spec_root = tmp_path / "docs" / "product"
    snapshots_home = tmp_path / ".product-spec-snapshots"
    with pytest.raises(snapshot.SpecRootMissingError):
        snapshot.make_snapshot(spec_root, snapshots_home, ts="20260101T000000")
    assert not snapshots_home.exists() or not list(snapshots_home.iterdir())


def test_audit_view_removed_from_catalog():
    # The `audit` governance view was never actually shippable (its
    # assembler was never built) — it is dropped from VIEWS entirely
    # instead of being advertised as a live --view choice that always fails.
    assert "audit" not in visualize.VIEWS
    with pytest.raises(SystemExit) as exc:
        _run_cli(["--root", str(_tmp_project()), "--view", "audit"])
    assert exc.value.code == 2


# ── Import-smoke: the whole render family loads + every ascii view runs ─────

@pytest.mark.parametrize("view", [
    "tree", "heatmap", "scope", "roadmap", "persona", "gap", "moscow", "time",
])
def test_ascii_graph_views_run_and_are_deterministic(view):
    g = _graph()
    kwargs = {}
    if view in ("tree", "roadmap", "time"):
        kwargs = {"lang": "en", "filter_wont": False}
    elif view == "persona":
        kwargs = {"filter_wont": False}
    elif view == "moscow":
        kwargs = {"lang": "en"}
    fn = getattr(render_ascii, view)
    a = fn(g, **kwargs)
    b = fn(g, **kwargs)
    assert isinstance(a, str) and a
    assert a == b


# ── off-enum bucket-drop must not silently vanish a node ────────────────────

def _graph_with_horizon(value):
    g = _graph()
    for n in g["nodes"]:
        if n["id"] == "PRD-AUTH":
            n["horizon"] = value
    return g


def test_roadmap_ascii_off_enum_horizon_not_dropped():
    g = _graph_with_horizon("someday")
    text = render_ascii.roadmap(g, lang="en", filter_wont=False)
    # Exact-line match: "PRD-AUTH" alone is a substring of "PRD-AUTH-E1" too,
    # so a loose `in` check would false-pass even with the bucket dropped.
    assert any(l.strip() == "- PRD-AUTH" for l in text.splitlines())


def test_time_ascii_off_enum_horizon_not_dropped():
    g = _graph_with_horizon("someday")
    text = render_ascii.time(g, lang="en", filter_wont=False)
    assert any(l.strip().startswith("- PRD-AUTH  [") for l in text.splitlines())


def test_dashboard_roadmap_html_off_enum_horizon_not_dropped():
    g = _graph_with_horizon("someday")
    html = render_html_governance._dashboard_roadmap(g, lang="en")
    assert '<th scope="row">PRD-AUTH</th>' in html


def test_scope_ascii_off_enum_scope_not_dropped():
    g = _graph()
    for n in g["nodes"]:
        if n["id"] == "PRD-AUTH-E1-S1":
            n["scope"] = "someday"
    text = render_ascii.scope(g)
    assert "1" in text.splitlines()[-1]  # the off-enum row's count survives


def test_persona_ascii_guards_non_list_product_personas():
    # graph["product"]["personas"] is now a raw pass-through (spec_graph
    # _product_meta no longer `or []`s a malformed scalar) so a hand-edited
    # `PRODUCT.md personas: 0` can reach here as a bare int, not a list.
    # render_ascii.persona() must degrade to its existing clean empty state
    # (guarded by isinstance(..., list)), never raise.
    g = _graph()
    g["product"]["personas"] = 0
    text = render_ascii.persona(g, filter_wont=False)
    assert isinstance(text, str)


def test_risk_ascii_off_enum_impact_not_dropped():
    g = _graph()
    g["risks"] = [{"impact": "critical", "likelihood": "high"}]
    text = render_ascii.risk(g)
    # The grid renders the fixed impact enum as row labels (not the raw
    # value), so the off-enum entry surfaces as a distinct "other" row.
    other_line = next(l for l in text.splitlines() if l.strip().startswith("| other"))
    assert "1" in other_line


def test_risk_ascii_off_enum_likelihood_not_dropped():
    # Sibling of the impact-axis guard above: a risk whose IMPACT is valid but
    # whose LIKELIHOOD is off-enum/typo'd (e.g. "medium" vs "med") must not
    # vanish. The old grid guarded only the impact axis, so an off-likelihood
    # count landed in a column the row-render never read back — the risk
    # disappeared with no trace. It must now surface in an 'other' column,
    # mirroring the HTML grid's (unrated) overflow row.
    g = _graph()
    g["risks"] = [{"impact": "high", "likelihood": "weird"}]
    text = render_ascii.risk(g)
    header = text.splitlines()[0]
    assert "other" in header  # off-likelihood surfaces a dedicated 'other' column
    # every risk is accounted for somewhere in the printed grid — total reconciles
    total = sum(
        int(tok)
        for line in text.splitlines()[2:]
        for tok in re.findall(r"\d+", line)
    )
    assert total == 1


def test_latest_alias_atomic_no_inplace_truncation(tmp_path, monkeypatch):
    # The <view>-latest.html pointer is the module's one long-lived, repeatedly
    # read artifact (browser tab / CI link). A direct shutil.copy2 onto it opens
    # the alias 'wb' — a truncate-then-refill window a concurrent reader can
    # observe as a 0-byte or half-written file. The fix copies to a temp in the
    # same dir then os.replace()s atomically. Proof: force the swap to fail; a
    # correct impl leaves the existing alias fully intact (never truncated).
    src = tmp_path / "tree-20260101T000000Z.html"
    src.write_text("<html>NEW-CONTENT</html>" * 500, encoding="utf-8")
    alias = tmp_path / "tree-latest.html"
    alias.write_text("OLD-COMPLETE", encoding="utf-8")

    def _boom(*_a, **_k):
        raise OSError("swap failed")

    monkeypatch.setattr(visuals_retention.os, "replace", _boom)
    with pytest.raises(OSError):
        visuals_retention.latest_alias(src)
    # the alias was never opened for in-place truncation — old content survives
    assert alias.read_text(encoding="utf-8") == "OLD-COMPLETE"


def test_strip_control_raises_on_key_collision_never_silent_drop():
    # A record read back from a hand-edited on-disk file may carry an arbitrary
    # key with an embedded control/bidi char (the writers re-serialize such keys
    # via dict(fm)/dict(m) on gate/update). If stripping collapses it onto an
    # existing key, the field must NOT silently vanish (last-wins) — the malformed
    # record is surfaced as a loud error instead. Clean records are unaffected.
    with pytest.raises(ValueError):
        render_common.strip_control({"no\x0bte": "first", "note": "second"})
    # a normal record round-trips untouched
    clean = {"id": "TASK-1", "title": "ok", "serves": ["S1"]}
    assert render_common.strip_control(clean) == clean


def test_latest_alias_writes_new_content(tmp_path):
    src = tmp_path / "tree-20260101T000000Z.html"
    src.write_text("<html>FRESH</html>", encoding="utf-8")
    alias = visuals_retention.latest_alias(src)
    assert alias.name == "tree-latest.html"
    assert alias.read_text(encoding="utf-8") == "<html>FRESH</html>"
    # no leftover temp files in the dir (mkstemp temp was renamed, not orphaned)
    assert not [p for p in src.parent.iterdir() if p.name.endswith(".tmp")]


# ── a colon in a PO-controlled title/id must not break Mermaid syntax ───────

def test_mermaid_roadmap_colon_in_title_one_event():
    g = _graph()
    for n in g["nodes"]:
        if n["id"] == "PRD-AUTH":
            n["title"] = "Q1: Launch MVP"
    text = render_mermaid.roadmap(g, lang="en", filter_wont=False)
    # A colon-bearing title must render as ONE timeline line for PRD-AUTH,
    # not split into extra bogus event segments. Mermaid's timeline splits a
    # line on EVERY raw `:` (regardless of surrounding spaces), so the only
    # `:` allowed on the line is the code-emitted field separator before the
    # section name — the title's own `:` must be neutralized.
    # Exact-prefix match on "PRD-AUTH —" (em-dash) excludes PRD-AUTH-E1's line.
    line = next(l for l in text.splitlines() if l.strip().startswith("PRD-AUTH —"))
    assert line.count(":") == 1


def test_mermaid_gantt_colon_in_id_does_not_break_row():
    g = _graph()
    for n in g["nodes"]:
        if n["id"] == "PRD-AUTH":
            n["id"] = "BRD-G:1"
            n["target_date"] = "2026-08-01"
    text = render_mermaid.time(g, lang="en", filter_wont=False)
    assert ":" not in render_mermaid._safe_label("BRD-G:1")
    assert "BRD-G:1" not in text


# ── label() must not crash on an unhashable `lang` ──────────────────────────

def test_label_unhashable_lang_does_not_crash():
    assert i18n_labels.label("now", lang=["vi"]) == i18n_labels.LABELS["en"]["now"]


# ── check_fence must degrade on a decode failure, never raise ───────────────

def test_check_fence_decode_failure_degrades_not_raises(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")
    monkeypatch.setattr(check_fence.subprocess, "run", _boom)
    findings = check_fence.scan(tmp_path)
    assert findings == []


# ── Dotfile-scrub hygiene: no `.claude/` reference survives in these files ──
# The banned literal is assembled (not written verbatim) so this repo-wide
# scanner file does not itself trip the harness's own
# test_bug_class_invariants::test_no_reference_to_claudekit_tree.
_DOTCLAUDE_RE = re.compile(r"\.claude/(?:%s|%s)/" % ("skills", "hooks"))


def test_no_dotclaude_refs_in_shipped_py_and_md():
    offenders = []
    for path in list(_SPEC_SCRIPTS.glob("*.py")) + list(_SPEC_REFERENCES.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if "# learn:" in line:
                continue  # whitelisted prose citation
            if _DOTCLAUDE_RE.search(line):
                offenders.append("%s:%d" % (path, line_no))
    assert offenders == [], "found .claude/ refs: %r" % offenders


def test_embed_spec_data_survives_exotic_dict_key():
    # board/explorer HTML data-island must not crash on a bytes dict key riding
    # in via a raw personas passthrough (same !!binary vector as O1); it now
    # routes through dumps_json, which key-coerces on the fail-soft retry.
    embed = _mods["render_html_assets"].embed_spec_data
    html = embed({"nodes": [{"id": "PRD-1", "personas": [{b"hello": "ok"}, "shopper"]}]})
    assert html.startswith("<script") and "b'hello'" in html


def test_inline_strips_terminal_escape_control_chars():
    # A PO title carrying a smuggled ANSI/OSC escape (ESC/BEL via a legal YAML
    # double-quoted scalar) must not ride into the rendered output where a
    # viewer's terminal would execute it — the shared _inline chokepoint strips
    # C0/DEL control bytes while leaving ordinary text intact.
    _inline = _mods["render_common"]._inline
    out = _inline("Evil\x1b[2J\x1b]0;PWNED\x07title")
    assert "\x1b" not in out and "\x07" not in out
    assert _inline("Hello  World\ttab") == "Hello World tab"   # normal ws-collapse intact


def test_mermaid_safe_label_strips_terminal_escape_control_chars():
    # The Mermaid label path shares the terminal-escape strip with the ASCII
    # path (`render_common._CONTROL_RE`): a C0 escape smuggled into a PO title
    # must not survive into a fenced ```mermaid block a viewer might `cat`.
    _safe_label = _mods["render_mermaid"]._safe_label
    out = _safe_label("Evil\x1b[2J\x07title")
    assert "\x1b" not in out and "\x07" not in out


def test_inline_strips_unicode_bidi_override():
    # Trojan-Source (CVE-2021-42574): a RIGHT-TO-LEFT OVERRIDE (U+202E) smuggled
    # into a PO title visually reverses/hides adjacent text in any viewer that
    # honors the Unicode bidi algorithm — a distinct mechanism from the C0/ANSI
    # escape class (U+202E is a 3-byte Cf format char, above the \x7f the C0
    # regex caps at). The shared _inline chokepoint must strip the bidi-control
    # block (LRE/RLE/PDF/LRO/RLO + the isolate set) too.
    _inline = _mods["render_common"]._inline
    out = _inline("Auth\u202eEVIL-HIDDEN\u202c PRD")
    assert "\u202e" not in out and "\u202c" not in out
    assert "Auth" in out and "EVIL-HIDDEN" in out and "PRD" in out


def test_mermaid_safe_label_strips_unicode_bidi_override():
    # Same shared _CONTROL_RE chokepoint: the Mermaid label path must strip the
    # bidi overrides so an RLO cannot reverse text in a rendered SVG diagram.
    _safe_label = _mods["render_mermaid"]._safe_label
    out = _safe_label("Auth\u202eEVIL\u202c")
    assert "\u202e" not in out and "\u202c" not in out


def test_html_heatmap_buckets_list_wrapped_status_like_ascii():
    # A hand-edit typo `status: [draft]` (list, not scalar) is flagged
    # invalid_type by check_consistency; the count-grid must SURFACE it in the
    # 'other' column, never silently coerce it into 'draft'. The HTML view must
    # agree with the ASCII view — previously HTML's _tip_scalar unwrapped the
    # 1-element list into 'draft' while ASCII's _hashable rendered it non-canonical.
    graph = {"nodes": [{"id": "PRD-A-E1-S1", "type": "story", "status": ["draft"]}]}
    ascii_out = render_ascii.heatmap(graph)
    html_out = render_html.heatmap(graph)
    assert "other" in ascii_out.lower()
    assert "other" in html_out.lower()


def test_html_is_deferred_node_agrees_with_render_common():
    # persona --filter-wont must classify a node identically in HTML and ASCII.
    # A list-wrapped `moscow: [wont]` is NOT the scalar 'wont' under a raw
    # compare, so the node is NOT deferred; HTML previously unwrapped it via
    # _tip_scalar and disagreed, filtering different stories out of each view.
    node = {"moscow": ["wont"], "scope": ["in"]}
    rc = _mods["render_common"]
    cg = _mods["render_html_count_grid"]
    assert cg._is_deferred_node(node) == rc._is_deferred(node)
    assert cg._is_deferred_node(node) is False


def test_html_board_col_label_gates_offenum_status_like_ascii():
    # render_board must localize ONLY the known horizon/MoSCoW column words (the
    # ascii board's _LOCALIZED_COLS gate), never every column value. An off-enum
    # status colliding with an i18n key (e.g. 'story') must render as the raw
    # column value in the html board — matching ascii — not the unrelated
    # artifact-type label 'Story'. Previously build_payload ran every column
    # through label(), diverging from the ascii board on the same graph.
    render_board = _mods["render_board"]
    graph = {"nodes": [{"id": "PRD-A-E1-S1", "type": "story",
                        "status": "story", "title": "t", "scope": "in"}]}
    payload = render_board.build_payload(graph, [], group_by="status", lang="en")
    assert payload["col_labels"].get("story") == "story"


def test_clean_old_renders_keep_zero_deletes_all(tmp_path):
    # keep=0 must delete EVERY timestamped render (retain none). The prior
    # `candidates[:-keep]` idiom broke here: -0 == 0, so `[:-0]` is `[:0]` == []
    # -> it deleted nothing instead of everything. Guard the public-API contract.
    vdir = tmp_path / "docs" / "product" / "visuals"
    vdir.mkdir(parents=True)
    for ts in ("20260101T000000", "20260102T000000", "20260103T000000"):
        (vdir / f"tree-{ts}.html").write_text("x", encoding="utf-8")
    deleted = visuals_retention.clean_old_renders(tmp_path, "tree", keep=0)
    assert len(deleted) == 3
    assert list(vdir.glob("tree-*.html")) == []


def test_clean_old_renders_orders_disambiguated_suffix_numerically(tmp_path):
    # Same-second collisions get a _N suffix (render_html._write_visual). FIFO
    # retention must order those NUMERICALLY, not lexicographically: "_10" is newer
    # than "_2" and must survive a keep-newest cull. A plain string sort keeps
    # _5.._9 and wrongly deletes _10.._12 (the genuinely newest three).
    vdir = tmp_path / "docs" / "product" / "visuals"
    vdir.mkdir(parents=True)
    ts = "20260101T000000Z"
    (vdir / f"tree-{ts}.html").write_text("first", encoding="utf-8")  # oldest (N=1)
    for n in range(2, 13):  # _2 .. _12, creation order == chronological
        (vdir / f"tree-{ts}_{n}.html").write_text(f"c{n}", encoding="utf-8")
    deleted = visuals_retention.clean_old_renders(tmp_path, "tree", keep=5)
    kept = {p.name for p in vdir.glob("tree-*.html")}
    assert kept == {f"tree-{ts}_{n}.html" for n in (8, 9, 10, 11, 12)}, \
        "must keep the 5 newest by numeric suffix, not lexicographic string order"
    assert len(deleted) == 7


def test_clean_old_renders_keep_two_retains_two_newest(tmp_path):
    # Reachable-path guard (keep>0 unchanged): keep the 2 newest, delete older.
    vdir = tmp_path / "docs" / "product" / "visuals"
    vdir.mkdir(parents=True)
    for ts in ("20260101T000000", "20260102T000000", "20260103T000000"):
        (vdir / f"tree-{ts}.html").write_text("x", encoding="utf-8")
    deleted = visuals_retention.clean_old_renders(tmp_path, "tree", keep=2)
    assert [p.name for p in deleted] == ["tree-20260101T000000.html"]
    assert sorted(p.name for p in vdir.glob("tree-*.html")) == [
        "tree-20260102T000000.html", "tree-20260103T000000.html"]


def _viz_bounded(fn, seconds=4):
    """Run fn() under SIGALRM so a blocking-read regression FAILS instead of
    hanging the whole suite forever."""
    import signal

    class _Blocked(Exception):
        pass

    def _handler(signum, frame):
        raise _Blocked

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_reuse_if_unchanged_skips_fifo_hash_sidecar(tmp_path):
    # The render-dedup hash sidecar is read on every visualize run. A FIFO/symlink
    # ->/dev/zero at that path (docs/product/visuals/) would block read_text
    # forever. Must skip a non-regular sidecar (treat as changed -> fresh render).
    import os
    hf = visuals_retention._hash_file(tmp_path, "tree")
    hf.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(hf)
    assert _viz_bounded(lambda: visuals_retention.reuse_if_unchanged(tmp_path, "tree", "<h>")) is None


def test_staleness_banner_skips_fifo_sig_sidecar(tmp_path):
    import os
    sf = visuals_retention._sig_file(tmp_path, "tree")
    sf.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(sf)
    out = _viz_bounded(lambda: visuals_retention.staleness_banner(tmp_path, "tree", {"nodes": []}))
    assert out == ""


def test_reuse_if_unchanged_non_dict_hash_sidecar_treated_as_stale(tmp_path):
    # A valid-but-non-object hash sidecar (JSON list from a hand-edit / bad merge)
    # passes json.loads but has no .get(); it must degrade to "no cache -> fresh
    # render" (return None), not crash the render pipeline with AttributeError.
    hf = visuals_retention._hash_file(tmp_path, "tree")
    hf.parent.mkdir(parents=True, exist_ok=True)
    hf.write_text('["not","a","dict"]', encoding="utf-8")
    assert visuals_retention.reuse_if_unchanged(tmp_path, "tree", "<h>") is None


def test_staleness_banner_non_dict_sig_sidecar_treated_as_fresh(tmp_path):
    # Same class on the signature sidecar: a non-dict json must read as "no baseline"
    # (return ""), never crash on data.get("node_ids").
    sf = visuals_retention._sig_file(tmp_path, "tree")
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text('["not","a","dict"]', encoding="utf-8")
    assert visuals_retention.staleness_banner(tmp_path, "tree", {"nodes": []}) == ""


def test_load_baseline_rejects_fifo_snapshot(tmp_path):
    # --snapshot baseline read: a FIFO/symlink->/dev/zero snapshot must not hang
    # _load_baseline; a non-regular snapshot is treated as corrupt (clean error).
    import os
    import pytest
    snap = tmp_path / "docs" / "product" / "visuals" / ".snapshots"
    snap.mkdir(parents=True)
    fifo = snap / "base.json"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        _viz_bounded(lambda: visualize._load_baseline(tmp_path, str(fifo)))


def test_write_visual_same_name_never_clobbers_disambiguates(tmp_path):
    # The one write chokepoint shared by all 12 graph views + board + explorer.
    # Two writes landing on the identical <view>-<ts>.html (same wall-clock second)
    # must NOT let the second silently truncate the first: disambiguate with a _N
    # suffix so both distinct-content renders survive on disk (the retention model
    # treats each timestamped file as one immutable snapshot). The suffix is '_' not
    # '-' so latest_alias's rsplit("-",1) view-derivation is not misled.
    t1 = render_html._write_visual(tmp_path, "tree-20260101T000000Z.html", "PAYLOAD-ONE")
    t2 = render_html._write_visual(tmp_path, "tree-20260101T000000Z.html", "PAYLOAD-TWO")
    assert t1 != t2, "second write reused the first path -> silent clobber"
    assert t1.read_text() == "PAYLOAD-ONE", "first render lost its own payload"
    assert t2.read_text() == "PAYLOAD-TWO"
    vdir = tmp_path / "docs" / "product" / "visuals"
    assert sorted(p.name for p in vdir.glob("tree-2026*.html")) == [
        "tree-20260101T000000Z.html", "tree-20260101T000000Z_2.html"], \
        "both same-second renders must coexist"


def test_html_write_same_second_two_renders_both_survive(tmp_path, monkeypatch):
    # End-to-end via render_html.write(): an edit -> re-render loop inside one second
    # produces two DIFFERENT-content renders; reuse_if_unchanged only short-circuits
    # byte-identical HTML, so distinct content forces the colliding write. Both must
    # survive — a captured first-render Path must never transparently serve the
    # second render's content.
    monkeypatch.setattr(render_html, "file_timestamp", lambda: "20260101T000000Z")
    g1 = {"nodes": [{"id": "STORY-A"}], "root_path": str(tmp_path)}
    g2 = {"nodes": [{"id": "STORY-A"}, {"id": "STORY-B"}], "root_path": str(tmp_path)}
    t1 = render_html.write(tmp_path, "tree", "html", "VIEW-ONE", g1, lang="en")
    first_content = t1.read_text()
    t2 = render_html.write(tmp_path, "tree", "html", "VIEW-TWO", g2, lang="en")
    assert t1 != t2, "same-second re-render reused the first path"
    assert t1.read_text() == first_content, "first render's content was overwritten"
    vdir = tmp_path / "docs" / "product" / "visuals"
    assert len(list(vdir.glob("tree-2026*.html"))) == 2, "both renders must persist"
    # The _N disambiguator must not confuse latest_alias's timestamp-strip: the
    # alias stays <view>-latest.html (pointing at the newest render), and no bogus
    # <view>-<ts>-latest.html is emitted from the disambiguated second write.
    assert (vdir / "tree-latest.html").read_text() == t2.read_text()
    assert not (vdir / "tree-20260101T000000Z-latest.html").exists()
