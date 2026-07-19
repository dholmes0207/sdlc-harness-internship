"""test_glossary_userproject — the glossary for a HARNESS-INSTALLER's own project.

A project that installs the harness gets its OWN glossary, sharing the SAME
register and the SAME four-field schema (term / definition / forbidden[] /
backing[]) as the harness-internal glossary — one register, two roots, no forked
schema. These tests exercise that end-user path under an arbitrary --root.

NOT marked dev_repo: this capability SHIPS, so the suite must stay green on an
installed copy (a dev_repo mark would red-skip it silently at deployer sites).
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import glossary_register as gr  # noqa: E402
import fs_guard  # noqa: E402

# The four content fields a glossary record carries, shared by BOTH scopes
# (harness-internal and end-user). A divergence here would mean a forked schema.
_SCHEMA_FIELDS = {"term", "definition", "forbidden", "backing", "actor", "ts"}


def test_migrate_scaffolds_empty_user_project(tmp_path):
    # an empty user project (no glossary at all) migrates to a YAML SSOT + a
    # marker-bearing view skeleton.
    res = gr.migrate(tmp_path)
    assert res["migrated"] == 0
    assert (tmp_path / "docs" / "glossary.yaml").is_file()
    view = tmp_path / "docs" / "GLOSSARY.md"
    assert view.is_file()
    assert gr.GENERATED_MARKER in view.read_text(encoding="utf-8")


def test_add_to_user_project_under_root(tmp_path):
    gr.migrate(tmp_path)
    gr.add_term(tmp_path, term="tenant",
                definition="An isolated customer space.",
                forbidden=["account", "org"],
                backing=["DEC-1", "test_tenant.py"])
    by = {t["term"]: t for t in gr.list_terms(tmp_path)}
    assert by["tenant"]["forbidden"] == ["account", "org"]
    assert by["tenant"]["backing"] == ["DEC-1", "test_tenant.py"]


def test_schema_matches_harness_internal(tmp_path):
    gr.migrate(tmp_path)
    gr.add_term(tmp_path, term="tenant", definition="d",
                forbidden=["x"], backing=["y"])
    rec = gr.list_terms(tmp_path)[0]
    assert set(rec.keys()) == _SCHEMA_FIELDS, (
        "the end-user record must share the harness schema, not fork it")


def test_writes_route_through_docs_zone_for_any_root(tmp_path):
    gr.migrate(tmp_path)
    yp = gr.add_term(tmp_path, term="tenant", definition="d", forbidden=[],
                     backing=[])
    assert Path(yp).resolve().is_relative_to((tmp_path / "docs").resolve())
    # the same guard refuses a write outside the docs zone, under any root
    with pytest.raises(fs_guard.FenceError):
        fs_guard.assert_under(tmp_path / "elsewhere.yaml", "docs", root=tmp_path)


def test_scaffold_is_idempotent_and_preserves_user_terms(tmp_path):
    gr.migrate(tmp_path)
    gr.add_term(tmp_path, term="tenant", definition="user content",
                forbidden=[], backing=[])
    # re-running the scaffold must NOT clobber the user's own terms
    gr.migrate(tmp_path)
    by = {t["term"]: t for t in gr.list_terms(tmp_path)}
    assert "tenant" in by and by["tenant"]["definition"] == "user content"
