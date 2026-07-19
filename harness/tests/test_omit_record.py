"""test_omit_record.py — the install-time omitted-skills record SSOT.

omit_record is the one place that knows the record's path, its JSON envelope,
and the skill-dir prefix. It is read/written by install, verify_install, hs-cli,
and the disabled-skill nudge, so the round-trip + the fail-open read arm (a
broken record must never silently hide a real drift) are load-bearing.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import omit_record  # noqa: E402


def test_record_path_is_under_state(tmp_path):
    p = omit_record.record_path(tmp_path)
    assert p == tmp_path / "harness" / "state" / "install-omitted-skills.json"


def test_write_then_read_round_trips(tmp_path):
    omit_record.write_omitted(tmp_path, ["drawio", "viz", "ai-sdk"])
    assert omit_record.read_omitted(tmp_path) == {"drawio", "viz", "ai-sdk"}


def test_write_sorts_and_creates_parent(tmp_path):
    omit_record.write_omitted(tmp_path, ["viz", "ai-sdk", "drawio"])
    import json
    data = json.loads(omit_record.record_path(tmp_path).read_text(encoding="utf-8"))
    assert data == {"omitted": ["ai-sdk", "drawio", "viz"]}  # sorted envelope


def test_read_missing_record_is_empty(tmp_path):
    # absent record -> nothing omitted (a fresh/full install), never a crash
    assert omit_record.read_omitted(tmp_path) == set()


def test_read_malformed_json_fails_open_to_empty(tmp_path):
    p = omit_record.record_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    # a broken record must not mask drift: degrade to "nothing omitted"
    assert omit_record.read_omitted(tmp_path) == set()


def test_read_drops_non_string_entries(tmp_path):
    p = omit_record.record_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"omitted": ["ok", 7, null, "fine"]}', encoding="utf-8")
    assert omit_record.read_omitted(tmp_path) == {"ok", "fine"}


def test_read_missing_key_is_empty(tmp_path):
    p = omit_record.record_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"other": 1}', encoding="utf-8")  # no "omitted" key
    assert omit_record.read_omitted(tmp_path) == set()


def test_skill_dir_prefixes_shape_and_skips_falsy():
    prefixes = omit_record.skill_dir_prefixes(["drawio", "", "viz"])
    assert prefixes == (
        "harness/plugins/hs/skills/drawio/",
        "harness/plugins/hs/skills/viz/",
    )
    # ready for str.startswith on a repo-relative path
    assert "harness/plugins/hs/skills/drawio/SKILL.md".startswith(prefixes)
