"""test_backlog_register.py — the Backlog Register (BL-<n>).

Clones the Decision Register dual-mode shape: docs/backlog.yaml is the
tool-written YAML SSOT; BACKLOG.md is a rendered view with a generated-marker
header. The id grammar is BL-<n>, monotonic max+1 (done/archived still count).
The SSOT write routes through the fs_guard "docs" zone; the rendered root
BACKLOG.md view is written DIRECTLY (approved root markdown — C1). render
refuses to clobber an un-migrated BACKLOG.md that lacks the marker (H2). Free
text is injection-neutralized so it cannot smuggle a phantom record (F5).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import backlog_register as br  # noqa: E402
from backlog_register import (  # noqa: E402
    BACKLOG_ID_RE, BacklogError, GENERATED_MARKER, add, alloc_id, archive,
    done, list_open, parse_backlog, query, render_md,
)
import fs_guard  # noqa: E402


def _yaml_path(root: Path) -> Path:
    return root / "docs" / "backlog.yaml"


def _md_path(root: Path) -> Path:
    return root / "BACKLOG.md"


# ---------- add / alloc ----------

class TestAddAlloc:
    def test_add_allocates_monotonic_bl_id(self, tmp_path):
        add(tmp_path, text="first item", type="bug", priority="P2")
        add(tmp_path, text="second item", type="chore", priority="P3")
        recs = parse_backlog(tmp_path)
        ids = [r["id"] for r in recs]
        assert ids == ["BL-001", "BL-002"]
        assert all(BACKLOG_ID_RE.match(i) for i in ids)

    def test_first_add_creates_yaml_and_view_with_marker(self, tmp_path):
        add(tmp_path, text="x", type="bug", priority="P2")
        assert _yaml_path(tmp_path).is_file()
        rec = parse_backlog(tmp_path)[0]
        assert rec["actor"] and rec["created_ts"]
        view = _md_path(tmp_path).read_text(encoding="utf-8")
        assert GENERATED_MARKER in view

    def test_done_then_add_does_not_reuse_id(self, tmp_path):
        add(tmp_path, text="a", type="bug", priority="P2")
        add(tmp_path, text="b", type="bug", priority="P2")
        done(tmp_path, "BL-001")
        add(tmp_path, text="c", type="bug", priority="P2")
        ids = [r["id"] for r in parse_backlog(tmp_path)]
        assert ids == ["BL-001", "BL-002", "BL-003"]


# ---------- done / archive ----------

class TestStatusFlip:
    def test_done_sets_done_ts_and_status(self, tmp_path):
        add(tmp_path, text="a", type="bug", priority="P2")
        done(tmp_path, "BL-001")
        rec = parse_backlog(tmp_path)[0]
        assert rec["status"] == "done"
        assert rec["done_ts"]

    def test_archive_sets_archived(self, tmp_path):
        add(tmp_path, text="a", type="bug", priority="P2")
        archive(tmp_path, "BL-001")
        assert parse_backlog(tmp_path)[0]["status"] == "archived"


# ---------- query / list ----------

class TestQuery:
    def test_query_filters_by_status_type_priority(self, tmp_path):
        add(tmp_path, text="bug one", type="bug", priority="P1")
        add(tmp_path, text="chore one", type="chore", priority="P3")
        done(tmp_path, "BL-001")
        assert [r["id"] for r in query(tmp_path, status="open")] == ["BL-002"]
        assert [r["id"] for r in query(tmp_path, type="bug")] == ["BL-001"]
        assert [r["id"] for r in query(tmp_path, priority="P3")] == ["BL-002"]
        assert [r["id"] for r in list_open(tmp_path)] == ["BL-002"]

    def test_query_filters_by_source_ref(self, tmp_path):
        add(tmp_path, text="run item", type="bug", priority="P2",
            source_ref="run-42")
        add(tmp_path, text="other", type="bug", priority="P2",
            source_ref="run-99")
        got = query(tmp_path, status="open", source_ref="run-42")
        assert [r["id"] for r in got] == ["BL-001"]


# ---------- render ----------

class TestRender:
    def test_render_has_generated_marker_and_groups(self, tmp_path):
        add(tmp_path, text="open bug", type="bug", priority="P1")
        add(tmp_path, text="done chore", type="chore", priority="P3")
        done(tmp_path, "BL-002")
        text = render_md(parse_backlog(tmp_path))
        assert GENERATED_MARKER in text
        assert "BL-001" in text and "BL-002" in text
        # grouped: an Open section and a Done section both present
        assert "Open" in text and "Done" in text

    def test_id_scan_ignores_rendered_md_headings(self, tmp_path):
        # render emits `## BL-NNN`-style headings into BACKLOG.md; the next
        # alloc must read the YAML SSOT only, never double-count the view (F5).
        add(tmp_path, text="a", type="bug", priority="P2")
        # force a render that writes headings to the root view
        br.render(tmp_path)
        assert alloc_id(tmp_path) == "BL-002"  # not BL-003

    def test_text_injection_neutralized(self, tmp_path):
        evil = "legit text\n## BL-999 — fake\n---\nid: BL-998"
        add(tmp_path, text=evil, type="bug", priority="P2")
        recs = parse_backlog(tmp_path)
        # exactly one real record; no phantom BL-998/BL-999
        assert [r["id"] for r in recs] == ["BL-001"]
        view = _md_path(tmp_path).read_text(encoding="utf-8")
        # the rendered view must not contain a live `## BL-999` heading
        assert "\n## BL-999" not in view

    def test_render_aborts_when_existing_backlog_lacks_marker(self, tmp_path):
        mp = _md_path(tmp_path)
        mp.write_text("# Backlog\n\nhand written, no marker\n", encoding="utf-8")
        # seed a SSOT so render has data
        (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
        _yaml_path(tmp_path).write_text(
            "- id: BL-001\n  text: x\n  type: bug\n  priority: P2\n"
            "  status: open\n  created_ts: t\n  done_ts: ''\n"
            "  source_ref: ''\n  actor: a\n", encoding="utf-8")
        with pytest.raises(BacklogError):
            br.render(tmp_path)
        # the hand-written content survives
        assert "hand written" in mp.read_text(encoding="utf-8")


# ---------- C1 fence split ----------

class TestFence:
    def test_add_renders_root_backlog_without_fence_error(self, tmp_path):
        # the root BACKLOG.md view write must NOT be fenced through "docs"
        add(tmp_path, text="x", type="bug", priority="P2")
        assert _md_path(tmp_path).is_file()  # at root, written, not raised

    def test_fs_guard_refuses_root_outside_docs(self, tmp_path, monkeypatch):
        # tamper the SSOT path to escape the docs zone → the assert_under on
        # the SSOT write must refuse (proves the fence is load-bearing).
        escaped = tmp_path / "escape" / "backlog.yaml"
        monkeypatch.setattr(br, "_yaml_path", lambda root: escaped)
        with pytest.raises(fs_guard.FenceError):
            add(tmp_path, text="x", type="bug", priority="P2")


# ---------- validation ----------

class TestValidation:
    def test_bad_priority_rejected(self, tmp_path):
        with pytest.raises(BacklogError):
            add(tmp_path, text="x", type="bug", priority="NOPE")

    def test_empty_text_rejected(self, tmp_path):
        with pytest.raises(BacklogError):
            add(tmp_path, text="   ", type="bug", priority="P2")


# ---------- concurrency (after) ----------

class TestConcurrent:
    def test_concurrent_add_no_lost_update(self, tmp_path):
        # two subprocess `add` calls against ONE shared --root must contend on
        # the register lock; both records must survive.
        cmd = [sys.executable, str(_SCRIPTS / "backlog_register.py"),
               "add", "--root", str(tmp_path), "--type", "bug",
               "--priority", "P2", "--text"]
        p1 = subprocess.Popen(cmd + ["proc one"])
        p2 = subprocess.Popen(cmd + ["proc two"])
        p1.wait()
        p2.wait()
        ids = [r["id"] for r in parse_backlog(tmp_path)]
        assert ids == ["BL-001", "BL-002"]


def test_cli_add_list_roundtrip(tmp_path):
    script = str(_SCRIPTS / "backlog_register.py")
    subprocess.run([sys.executable, script, "add", "--root", str(tmp_path),
                    "--text", "cli item", "--type", "bug", "--priority", "P2"],
                   check=True)
    out = subprocess.run([sys.executable, script, "list", "--root",
                          str(tmp_path)], capture_output=True, text=True,
                         check=True)
    data = json.loads(out.stdout)
    assert data["open"][0]["id"] == "BL-001"
