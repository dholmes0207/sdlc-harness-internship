"""persona_me — the per-user RELATIONSHIP (PII) tier.

RELATIONSHIP is PII the user self-declares (name/role/relationship/occupation/…),
stored in a gitignored per-user JSON file (default ~/.claude/persona-me.json, env
seam HARNESS_PERSONA_ME). The read path never raises; the write path validates
maxlen AND — critically (F9) — writes the sibling .gitignore BEFORE the JSON, so a
gitignore failure leaves NO un-ignored PII on disk. Every test seams the path to a
tmp dir; none touches the real ~/.claude.
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import persona_me  # noqa: E402

_ENV = "HARNESS_PERSONA_ME"


# --- path resolution ---

def test_me_path_default_is_home_claude(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    assert persona_me.me_path() == Path.home() / ".claude" / "persona-me.json"


def test_me_path_env_override(tmp_path, monkeypatch):
    target = tmp_path / "x.json"
    monkeypatch.setenv(_ENV, str(target))
    assert persona_me.me_path() == target


# --- read path: never-raise ---

def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(_ENV, str(tmp_path / "nope.json"))
    assert persona_me.load() is None


def test_load_malformed_json_returns_none(tmp_path, monkeypatch):
    p = tmp_path / "persona-me.json"
    p.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv(_ENV, str(p))
    assert persona_me.load() is None


def test_load_non_mapping_returns_none(tmp_path, monkeypatch):
    p = tmp_path / "persona-me.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setenv(_ENV, str(p))
    assert persona_me.load() is None


def test_load_valid_returns_fields(tmp_path, monkeypatch):
    p = tmp_path / "persona-me.json"
    p.write_text(json.dumps({"name": "Hieu", "role": "owner"}), encoding="utf-8")
    monkeypatch.setenv(_ENV, str(p))
    got = persona_me.load()
    assert got["name"] == "Hieu"
    assert got["role"] == "owner"


def test_load_field_over_maxlen_dropped(tmp_path, monkeypatch):
    p = tmp_path / "persona-me.json"
    p.write_text(json.dumps({"name": "Hieu", "role": "x" * 200}), encoding="utf-8")
    monkeypatch.setenv(_ENV, str(p))
    got = persona_me.load()  # tolerant: over-limit field dropped, no raise
    assert got is not None
    assert "role" not in got
    assert got["name"] == "Hieu"


# --- write path: maxlen raises ---

def test_save_over_maxlen_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(_ENV, str(tmp_path / "persona-me.json"))
    with pytest.raises(persona_me.PersonaMeError):
        persona_me.save({"role": "x" * 200})       # > 150 default cap
    with pytest.raises(persona_me.PersonaMeError):
        persona_me.save({"name": "x" * 41})        # > 40 name cap


# --- write path: gitignore-before-JSON (F9) ---

def test_save_creates_gitignore_sibling(tmp_path, monkeypatch):
    target = tmp_path / "persona-me.json"
    monkeypatch.setenv(_ENV, str(target))
    persona_me.save({"name": "Hieu"})
    gi = tmp_path / ".gitignore"
    assert gi.exists()
    assert "persona-me.json" in gi.read_text(encoding="utf-8")
    assert target.exists()


def test_save_appends_to_existing_gitignore_no_dup(tmp_path, monkeypatch):
    target = tmp_path / "persona-me.json"
    gi = tmp_path / ".gitignore"
    gi.write_text("other-file\n", encoding="utf-8")
    monkeypatch.setenv(_ENV, str(target))
    persona_me.save({"name": "Hieu"})
    txt = gi.read_text(encoding="utf-8")
    assert "other-file" in txt
    assert "persona-me.json" in txt
    persona_me.save({"name": "Hieu2"})  # second save must not duplicate the line
    assert gi.read_text(encoding="utf-8").count("persona-me.json") == 1


def test_gitignore_written_before_json_failclosed(tmp_path, monkeypatch):
    # F9: if the .gitignore write FAILS, save raises and NO JSON PII is left behind.
    target = tmp_path / "persona-me.json"
    monkeypatch.setenv(_ENV, str(target))
    (tmp_path / ".gitignore").mkdir()  # a dir where the file must go → write fails
    with pytest.raises(persona_me.PersonaMeError):
        persona_me.save({"name": "Hieu"})
    assert not target.exists()  # fail-closed: no un-ignored PII on disk


def test_save_gitignore_non_utf8_raises_persona_me_error(tmp_path, monkeypatch):
    # A sibling .gitignore with invalid UTF-8 (written by some other tool in latin-1)
    # must surface as the documented PersonaMeError — not a raw UnicodeDecodeError —
    # and must still leave NO PII JSON on disk (the read happens before the JSON write).
    target = tmp_path / "persona-me.json"
    monkeypatch.setenv(_ENV, str(target))
    (tmp_path / ".gitignore").write_bytes(b"other-file\n\xe9\xff\n")  # invalid UTF-8
    with pytest.raises(persona_me.PersonaMeError):
        persona_me.save({"name": "Hieu"})
    assert not target.exists()
