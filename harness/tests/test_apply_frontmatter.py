"""test_apply_frontmatter.py — retired auto-fill script (S2 standardization).

apply_frontmatter used to auto-fill `category`/`license`/`keywords` on skills that
lacked them. Those fields were retired (no consumer, dead listing-budget weight) —
the schema (harness/schemas/skill-schema.json) no longer declares them, so this
script must never regenerate them. It is kept as a no-op stub (not deleted) so
existing imports/tooling do not break; `main()` reports and never mutates a file.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import apply_frontmatter as af  # noqa: E402


_SKILL = """---
name: demo
description: Demo skill. Use when demoing.
metadata:
  compliance-tier: workflow
---

# body
"""


def test_retired_fields_are_not_regenerated():
    # The retired auto-fill surface must be gone — no function left that could
    # be called to resurrect category/license/keywords.
    for retired_symbol in ("compute_missing", "_fmt_lines", "derive_keywords",
                           "GROUP_TO_CATEGORY"):
        assert not hasattr(af, retired_symbol), (
            "%r must be removed from apply_frontmatter — it regenerates a retired field"
            % retired_symbol)


def test_main_never_mutates_a_skill_md(tmp_path, capsys):
    plugins = tmp_path / "harness" / "plugins" / "hs" / "skills" / "demo"
    plugins.mkdir(parents=True)
    skill_md = plugins / "SKILL.md"
    skill_md.write_text(_SKILL, encoding="utf-8")
    before = skill_md.read_text(encoding="utf-8")

    rc = af.main(["--root", str(tmp_path)])

    assert rc == 0
    assert skill_md.read_text(encoding="utf-8") == before  # untouched, byte-for-byte


def test_main_reports_retired_and_zero_changes(capsys):
    rc = af.main(["--root", "."])
    assert rc == 0
    out = capsys.readouterr().out
    assert "retired" in out.lower()
