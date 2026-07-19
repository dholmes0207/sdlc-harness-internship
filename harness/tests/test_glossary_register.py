"""test_glossary_register — the Glossary Register (shared-language SSOT).

Mirrors the Decision Register machinery, with one deliberate difference: the
key is the `term` itself (no monotonic id — a glossary is looked up by name,
not numbered, because terms never supersede). Records carry array-valued
`forbidden` and `backing` fields. Storage is dual-mode: docs/glossary.yaml is
the YAML SSOT when present, the legacy markdown table is the source when it is
not, and docs/GLOSSARY.md is the rendered view (GENERATED_MARKER + no-clobber).
Every write resolves through the fs_guard "docs" zone.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import glossary_register as gr  # noqa: E402
import fs_guard  # noqa: E402 — class looked up live (other tests reload it)

_SCRIPT = _SCRIPTS / "glossary_register.py"

# The fs_guard ban held as a pattern so this file never carries the contiguous
# banned string (the harness-wide scan in test_bug_class_invariants would flag
# it otherwise — same self-consistency trick as test_glossary_invariants).
_BANNED_FS = re.compile(r"write[- ]fence", re.I)


def _seed_yaml(root: Path, records):
    p = root / "docs" / "glossary.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(records, sort_keys=False, allow_unicode=True),
                 encoding="utf-8")
    return p


def test_add_then_list_carries_arrays(tmp_path):
    # Exercise the WRITE path with non-default, multi-element arrays for BOTH
    # forbidden and backing (a field is only truly persisted if every read AND
    # write path carries it — LESSONS round-trip).
    _seed_yaml(tmp_path, [])
    gr.add_term(tmp_path, term="widget",
                definition="A reusable UI part.",
                forbidden=["gadget", "doohickey"],
                backing=["DEC-9", "test_widget.py"])
    terms = gr.list_terms(tmp_path)
    by = {t["term"]: t for t in terms}
    assert "widget" in by
    assert by["widget"]["forbidden"] == ["gadget", "doohickey"]
    assert by["widget"]["backing"] == ["DEC-9", "test_widget.py"]


def test_add_duplicate_term_is_rejected(tmp_path):
    _seed_yaml(tmp_path, [])
    gr.add_term(tmp_path, term="widget", definition="first", forbidden=[],
                backing=[])
    with pytest.raises(gr.GlossaryError):
        gr.add_term(tmp_path, term="widget", definition="second",
                    forbidden=[], backing=[])
    # the first definition survives — no silent overwrite
    by = {t["term"]: t for t in gr.list_terms(tmp_path)}
    assert by["widget"]["definition"] == "first"


def test_render_has_marker_columns_and_joins(tmp_path):
    _seed_yaml(tmp_path, [
        {"term": "alpha", "definition": "first", "forbidden": ["x", "y"],
         "backing": ["DEC-1", "t_a.py"]},
    ])
    md = gr.render_md(gr.list_terms(tmp_path))
    assert gr.GENERATED_MARKER in md
    for col in ("Term", "Definition", "Forbidden", "Backing"):
        assert col in md
    assert "x / y" in md          # forbidden joined with " / "
    assert "DEC-1; t_a.py" in md  # backing joined with "; "


def test_render_is_deterministic_sorted_by_term(tmp_path):
    _seed_yaml(tmp_path, [
        {"term": "zeta", "definition": "z", "forbidden": [], "backing": []},
        {"term": "alpha", "definition": "a", "forbidden": [], "backing": []},
    ])
    md = gr.render_md(gr.list_terms(tmp_path))
    assert md.index("alpha") < md.index("zeta")
    # pure function of the records → repeatable
    assert md == gr.render_md(gr.list_terms(tmp_path))


def test_check_roundtrip_then_drift(tmp_path):
    _seed_yaml(tmp_path, [
        {"term": "alpha", "definition": "a", "forbidden": [], "backing": []},
    ])
    # render writes GLOSSARY.md from the SSOT
    rc = subprocess.run([sys.executable, str(_SCRIPT), "--root", str(tmp_path),
                         "--render"], capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr
    # immediately after render the view is in sync → --check exit 0
    rc = subprocess.run([sys.executable, str(_SCRIPT), "--root", str(tmp_path),
                         "--check"], capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr
    # mutate the SSOT → --check detects drift, exit 1
    _seed_yaml(tmp_path, [
        {"term": "alpha", "definition": "MUTATED", "forbidden": [],
         "backing": []},
    ])
    rc = subprocess.run([sys.executable, str(_SCRIPT), "--root", str(tmp_path),
                         "--check"], capture_output=True, text=True)
    assert rc.returncode == 1, "drift must exit 1\n%s" % rc.stdout


def test_render_refuses_to_clobber_hand_authored_view(tmp_path):
    _seed_yaml(tmp_path, [
        {"term": "alpha", "definition": "a", "forbidden": [], "backing": []},
    ])
    view = tmp_path / "docs" / "GLOSSARY.md"
    view.write_text("# Hand-authored, no marker\n", encoding="utf-8")
    rc = subprocess.run([sys.executable, str(_SCRIPT), "--root", str(tmp_path),
                         "--render"], capture_output=True, text=True)
    assert rc.returncode == 2, "no-clobber must refuse a marker-less view"
    assert "# Hand-authored, no marker" in view.read_text(encoding="utf-8")
    # --force overrides
    rc = subprocess.run([sys.executable, str(_SCRIPT), "--root", str(tmp_path),
                         "--render", "--force"], capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr
    assert gr.GENERATED_MARKER in view.read_text(encoding="utf-8")


def test_injection_in_term_or_definition_cannot_forge_a_row(tmp_path):
    _seed_yaml(tmp_path, [
        {"term": "alpha\n## Fake heading", "definition": "line one\n---\nline two",
         "forbidden": [], "backing": []},
    ])
    md = gr.render_md(gr.list_terms(tmp_path))
    # no smuggled heading, no smuggled fence line, table still one data row
    body = "\n".join(md.splitlines()[7:])  # skip frontmatter (7 lines)
    assert not any(ln.startswith("## ") for ln in body.splitlines())
    assert not any(ln.strip() == "---" for ln in body.splitlines())
    data_rows = [ln for ln in body.splitlines()
                 if ln.startswith("| ") and "alpha" in ln]
    assert len(data_rows) == 1


def test_writes_stay_inside_docs_zone(tmp_path):
    _seed_yaml(tmp_path, [])
    yp = gr.add_term(tmp_path, term="widget", definition="d", forbidden=[],
                     backing=[])
    assert Path(yp).resolve().is_relative_to((tmp_path / "docs").resolve())
    # a path outside the docs zone is refused by the same guard the writer uses
    with pytest.raises(fs_guard.FenceError):
        fs_guard.assert_under(tmp_path / "evil.yaml", "docs", root=tmp_path)


def test_migrate_legacy_md_to_yaml(tmp_path):
    # a legacy hand-authored table (no SSOT yet) migrates to glossary.yaml +
    # a rendered, marker-bearing view, preserving terms/forbidden/backing.
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    (docs / "GLOSSARY.md").write_text(
        "# Glossary\n\n"
        "| Term | Definition | Forbidden wording | Backing |\n"
        "|---|---|---|---|\n"
        "| `alpha` | first term | bad-alpha | DEC-1; t_a.py |\n"
        "| `beta` | second term |  | DEC-2 |\n",
        encoding="utf-8")
    rc = subprocess.run([sys.executable, str(_SCRIPT), "--root", str(tmp_path),
                         "--migrate", "--force"], capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr
    assert (docs / "glossary.yaml").is_file()
    by = {t["term"]: t for t in gr.list_terms(tmp_path)}
    assert "`alpha`" in by and "`beta`" in by
    assert by["`alpha`"]["backing"] == ["DEC-1", "t_a.py"]   # split on ";"
    assert by["`alpha`"]["forbidden"] == ["bad-alpha"]       # verbatim element
    assert by["`beta`"]["forbidden"] == []                   # empty cell → []
    assert gr.GENERATED_MARKER in (docs / "GLOSSARY.md").read_text("utf-8")


def test_add_via_cli_emits_finding_on_duplicate(tmp_path):
    _seed_yaml(tmp_path, [])
    base = [sys.executable, str(_SCRIPT), "--root", str(tmp_path), "--add",
            "--term", "widget", "--definition", "d"]
    rc = subprocess.run(base, capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr
    rc = subprocess.run(base, capture_output=True, text=True)
    # analytical-script contract: bad input → JSON finding on stdout, exit 0
    assert rc.returncode == 0
    out = json.loads(rc.stdout)
    assert out.get("written") is False and "error" in out
