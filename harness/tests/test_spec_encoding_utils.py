"""hs:spec — encoding_utils sink hardening: a lone UTF-16 surrogate code point
(how os.fsdecode surrogate-escapes a non-UTF-8 filename) must not crash the
UTF-8 write chokepoints emit_json (stdout) and write_text_atomic (file). One
shared source feeds every spec + shape CLI, so a crash here breaks the
"always exit 0, emit valid JSON" contract across the whole surface."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

eu = load_skill_scripts(_SPEC_SCRIPTS, ["encoding_utils"])["encoding_utils"]

_SURR = "\udcff"  # a lone low surrogate (os.fsdecode of a stray 0xFF filename byte)


def _has_surrogate(s):
    return any(0xD800 <= ord(c) <= 0xDFFF for c in s)


def _utf8_capture(monkeypatch):
    """Replace sys.stdout with a REAL strict-UTF-8 text stream over a byte buffer,
    so a lone surrogate hits the same UnicodeEncodeError the shell stdout raises
    (pytest's own capture would mask it)."""
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="utf-8", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    return buf, stream


def test_emit_json_survives_lone_surrogate(monkeypatch):
    buf, stream = _utf8_capture(monkeypatch)
    eu.emit_json({"file": f"weird_{_SURR}_name.md", "ok": "Việt tiếng ü"})
    stream.flush()
    text = buf.getvalue().decode("utf-8")   # valid UTF-8 on the wire — no crash
    obj = json.loads(text)                  # and still valid JSON
    assert not _has_surrogate(obj["file"])  # surrogate replaced, none survives
    assert "�" in obj["file"]          # with the Unicode replacement char
    assert obj["ok"] == "Việt tiếng ü"      # real unicode untouched


def test_emit_json_clean_payload_byte_identical(monkeypatch):
    # No surrogate -> the fast path is unchanged: no replacement char injected.
    buf, stream = _utf8_capture(monkeypatch)
    eu.emit_json({"file": "clean_name.md", "vn": "Xin chào ü"})
    stream.flush()
    text = buf.getvalue().decode("utf-8")
    assert "�" not in text
    assert json.loads(text)["vn"] == "Xin chào ü"


def test_write_text_atomic_survives_lone_surrogate(tmp_path):
    p = tmp_path / "matrix.txt"
    eu.write_text_atomic(p, f"row weird_{_SURR}_name.md | covered\n")  # must not raise
    data = p.read_bytes().decode("utf-8")   # file is valid UTF-8
    assert not _has_surrogate(data)
    assert "�" in data


def test_write_text_atomic_clean_text_byte_identical(tmp_path):
    p = tmp_path / "clean.txt"
    body = "row clean_name.md | ü covered\n"
    eu.write_text_atomic(p, body)
    assert p.read_bytes() == body.encode("utf-8")  # no sanitize on the happy path


def test_check_consistency_cli_exits_clean_on_surrogate_filename(tmp_path):
    # End-to-end: a real invalid-UTF-8-named artifact under docs/product/ rides
    # its surrogate-escaped path into the graph's `file` field and out through
    # emit_json. The CLI (docstring: "Always exits 0") must stay exit 0 with
    # parseable JSON, not a raw traceback + accidental exit 1.
    prod = tmp_path / "docs" / "product"
    (prod / "prds").mkdir(parents=True)
    (prod / "PRODUCT.md").write_text("---\nid: PRODUCT\ntype: product\n---\n# P\n",
                                     encoding="utf-8")
    bad = os.fsencode(str(prod / "prds")) + b"/weird_\xff_name.md"
    with open(bad, "wb") as fh:
        fh.write(b"---\nid: PRD-BAD\ntype: prd\ntitle: x\nstatus: draft\n"
                 b"scope: in\nmoscow: must\nhorizon: now\nsize: M\nlang: en\n---\n# x\n")
    script = _SPEC_SCRIPTS / "check_consistency.py"
    out = subprocess.run([sys.executable, str(script), "--root", str(tmp_path)],
                         capture_output=True)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    payload = json.loads(out.stdout.decode("utf-8"))  # valid JSON, no surrogate on wire
    assert payload["artifacts_checked"] >= 1 if "artifacts_checked" in payload else True
