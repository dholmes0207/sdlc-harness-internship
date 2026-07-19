"""test_backlog_migrate.py — one-time BACKLOG.md → backlog.yaml migration.

RT-1: the current hand-written BACKLOG.md is archived BYTE-EQUAL into
docs/BACKLOG-archive.md BEFORE any parse, and the render that overwrites
BACKLOG.md REFUSES to run unless that archive equality still holds — so the
single destructive write in the plan can never lose prose. Only currently-open
items (`- [ ]`) migrate; closed history stays in the archive verbatim.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import backlog_migrate as bm  # noqa: E402
import backlog_register as br  # noqa: E402

_FIXTURE = """# BACKLOG — sample

Intro prose, dates, links — must survive byte-equal in the archive.

## Open section

- [ ] **first open item** — needs doing, report: plans/reports/x.md
- [x] done item — already closed, stays only in the archive
- [ ] 🔴 second open item, high priority
- [ ] 🟡 third open item, low priority

## Notes

> a blockquote of context that the flat view would drop
"""


def _seed_backlog(root: Path, text: str = _FIXTURE) -> Path:
    p = root / "BACKLOG.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_archive_is_byte_equal_to_source(tmp_path):
    src = _seed_backlog(tmp_path)
    arch = bm.archive_backlog(tmp_path)
    assert arch.read_bytes() == src.read_bytes()


def test_archive_lives_under_docs(tmp_path):
    _seed_backlog(tmp_path)
    arch = bm.archive_backlog(tmp_path)
    assert arch == tmp_path / "docs" / "BACKLOG-archive.md"


def test_parse_open_items_excludes_closed(tmp_path):
    items = bm.parse_open_items(_FIXTURE)
    texts = [i["text"] for i in items]
    assert len(items) == 3  # the three `- [ ]`, not the `- [x]`
    assert any("first open item" in t for t in texts)
    assert not any("done item" in t for t in texts)


# richer parse: section provenance + content-based type inference
_SECTIONED = """# BACKLOG

## Mở 2026-06-26 — VSF skill bundle

- [ ] **`vsf:jira` skill** — discovery-first push, idempotent
- [ ] 🔴 Sửa kiến trúc — bẫy waterfall (chí mạng)

## Mở 2026-06-20 — gate hardening

- [ ] tighten the DoD gate keying
"""


def test_parse_captures_section_provenance():
    items = bm.parse_open_items(_SECTIONED)
    # every item carries the enclosing `## ` section heading as source_ref
    assert items[0]["source_ref"].startswith("Mở 2026-06-26 — VSF skill bundle")
    assert items[2]["source_ref"].startswith("Mở 2026-06-20 — gate hardening")


def test_parse_infers_type_from_content():
    items = bm.parse_open_items(_SECTIONED)
    by_text = {i["text"][:12]: i for i in items}
    # a "skill" item is a feature; a "kiến trúc" item is architecture
    assert by_text["**`vsf:jira`"]["type"] == "feature"
    assert items[1]["type"] == "architecture"
    # a plain item defaults to debt
    assert items[2]["type"] == "debt"


def test_parse_priority_from_glyph_in_sectioned():
    items = bm.parse_open_items(_SECTIONED)
    assert items[1]["priority"] == "P1"  # 🔴
    assert items[0]["priority"] == "P2"  # no glyph


def test_open_items_become_open_records(tmp_path):
    _seed_backlog(tmp_path)
    bm.archive_backlog(tmp_path)
    candidates = bm.parse_open_items(_FIXTURE)
    bm.apply_migration(tmp_path, candidates)
    recs = br.parse_backlog(tmp_path)
    assert len(recs) == 3
    assert all(r["status"] == "open" for r in recs)


def test_flip_aborts_if_archive_not_byte_equal(tmp_path):
    src = _seed_backlog(tmp_path)
    bm.archive_backlog(tmp_path)
    # corrupt the archive AFTER it was taken
    (tmp_path / "docs" / "BACKLOG-archive.md").write_text("tampered\n",
                                                          encoding="utf-8")
    before = src.read_bytes()
    with pytest.raises(bm.MigrationError):
        bm.apply_migration(tmp_path, bm.parse_open_items(_FIXTURE))
    # the source BACKLOG.md must be untouched — the flip never ran
    assert src.read_bytes() == before


def test_flip_aborts_if_archive_missing(tmp_path):
    _seed_backlog(tmp_path)
    # no archive taken at all → flip must refuse
    with pytest.raises(bm.MigrationError):
        bm.apply_migration(tmp_path, bm.parse_open_items(_FIXTURE))


def test_rendered_backlog_is_idempotent(tmp_path):
    _seed_backlog(tmp_path)
    bm.archive_backlog(tmp_path)
    bm.apply_migration(tmp_path, bm.parse_open_items(_FIXTURE))
    first = (tmp_path / "BACKLOG.md").read_bytes()
    br.render(tmp_path)  # re-render from the SSOT
    assert (tmp_path / "BACKLOG.md").read_bytes() == first


def test_flipped_backlog_has_generated_marker(tmp_path):
    _seed_backlog(tmp_path)
    bm.archive_backlog(tmp_path)
    bm.apply_migration(tmp_path, bm.parse_open_items(_FIXTURE))
    assert br.GENERATED_MARKER in (tmp_path / "BACKLOG.md").read_text(
        encoding="utf-8")
