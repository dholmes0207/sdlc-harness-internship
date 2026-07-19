"""test_register_store.py — shared append-only fenced-record machinery.

Ported PS semantics: RECORD_RE block splitting, escape_injection (bare `---`
fence + register-heading anchors neutralized by backslash, text preserved),
register_lock (best-effort flock critical section), scan_record_ids (raw id
scan that counts corrupt-but-id-bearing blocks).

Harness additions: sanitize_field() collapses newlines so single-line fields
(title/affects) cannot smuggle a phantom record or heading; a degraded lock
(no fcntl / flock refusal) WARNS about the concurrent-overwrite risk instead
of passing silently — once per process, never blocking.
"""
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import register_store  # noqa: E402
from register_store import (  # noqa: E402
    RECORD_RE, escape_injection, register_lock, sanitize_field, scan_record_ids,
)

_DEC_HEADING_RE = re.compile(r"(?m)^(##\s+DEC-)")


class TestRecordRe:
    def test_splits_fenced_blocks(self):
        text = (
            "# Register\n\n"
            "---\nid: DEC-1\nstatus: active\n---\n## DEC-1 — a\n\nbody one\n"
            "---\nid: DEC-2\nstatus: active\n---\n## DEC-2 — b\n\nbody two\n"
        )
        blocks = list(RECORD_RE.finditer(text))
        assert len(blocks) == 2
        assert "id: DEC-1" in blocks[0].group("fm")
        assert "body two" in blocks[1].group("body")


class TestEscapeInjection:
    def test_bare_fence_line_is_escaped(self):
        out = escape_injection("before\n---\nafter", _DEC_HEADING_RE)
        assert "\n\\---\n" in out
        assert "before" in out and "after" in out

    def test_heading_line_is_escaped(self):
        out = escape_injection("x\n## DEC-9 — fake\ny", _DEC_HEADING_RE)
        assert "\n\\## DEC-9" in out

    def test_plain_text_unchanged(self):
        s = "a normal rationale — with --- inline and ## not-a-dec heading"
        assert escape_injection(s, _DEC_HEADING_RE) == s

    def test_none_treated_as_empty(self):
        assert escape_injection(None, _DEC_HEADING_RE) == ""


class TestSanitizeField:
    def test_newlines_collapsed_to_spaces(self):
        out = sanitize_field("line1\nline2\r\nline3", _DEC_HEADING_RE)
        assert "\n" not in out and "\r" not in out
        assert "line1" in out and "line3" in out

    def test_field_cannot_smuggle_phantom_record(self):
        # A title carrying a full fenced block must come out as ONE inert line.
        evil = "ok\n---\nid: DEC-99\n---\n## DEC-99 — fake"
        out = sanitize_field(evil, _DEC_HEADING_RE)
        assert "\n" not in out
        assert not RECORD_RE.search("## H\n" + out + "\n")

    def test_plain_field_unchanged(self):
        assert sanitize_field("use flock for appends", _DEC_HEADING_RE) == \
            "use flock for appends"


class TestRegisterLock:
    def test_lock_creates_parent_and_yields(self, tmp_path):
        lock = tmp_path / "deep" / ".reg.lock"
        ran = []
        with register_lock(lock):
            ran.append(True)
        assert ran == [True]
        assert lock.parent.is_dir()

    def test_degraded_lock_warns_once_about_concurrent_risk(self, tmp_path, monkeypatch, capsys):
        # No usable flock → warn (concurrent agents may overwrite each other),
        # exactly once per process, and still proceed (never block the write).
        monkeypatch.setattr(register_store, "_flock", lambda fh, op: False)
        monkeypatch.setattr(register_store, "_warned_degraded", False)
        with register_lock(tmp_path / ".l1"):
            pass
        with register_lock(tmp_path / ".l2"):
            pass
        err = capsys.readouterr().err
        assert err.count("concurrent") == 1


class TestScanRecordIds:
    def test_scans_all_ids_including_corrupt_yaml_blocks(self):
        text = (
            "---\nid: DEC-1\nstatus: active\n---\nbody\n"
            "---\nid: DEC-5\naffects: [unterminated\n---\nbody\n"
        )
        assert scan_record_ids(text) == ["DEC-1", "DEC-5"]

    def test_empty_text_no_ids(self):
        assert scan_record_ids("") == []
        assert scan_record_ids("# header only\n") == []
