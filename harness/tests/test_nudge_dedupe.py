"""test_nudge_dedupe.py — per-(session, kind, subject) marker so a repeated breach
nudges ONCE per session. Fail-open: a marker error must NOT crash the hook (it nudges)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks"))
import nudge_dedupe as nd  # noqa: E402


def test_first_call_false_then_true_after_mark(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    assert nd.already_nudged("s1", "memory_gap", "docs/x.md") is False
    nd.mark_nudged("s1", "memory_gap", "docs/x.md")
    assert nd.already_nudged("s1", "memory_gap", "docs/x.md") is True


def test_different_subject_or_session_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    nd.mark_nudged("s1", "memory_gap", "a")
    assert nd.already_nudged("s1", "memory_gap", "b") is False       # subject differs
    assert nd.already_nudged("s2", "memory_gap", "a") is False       # session differs
    assert nd.already_nudged("s1", "standards_drift", "a") is False  # kind differs


def test_fail_open_on_unwritable_tempdir(tmp_path, monkeypatch):
    # An unwritable/broken marker dir must degrade to "not nudged" (nudge, not crash).
    bad = tmp_path / "nope"
    bad.write_text("i am a file, not a dir", encoding="utf-8")
    monkeypatch.setenv("TMPDIR", str(bad))
    assert nd.already_nudged("s1", "memory_gap", "a") is False   # never raises
    nd.mark_nudged("s1", "memory_gap", "a")                      # swallows the error
    assert nd.already_nudged("s1", "memory_gap", "a") is False
