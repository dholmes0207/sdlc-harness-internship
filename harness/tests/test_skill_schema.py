"""test_skill_schema.py — SKILL.md frontmatter contract (B2 merge into check_skill_structure).

skill-schema.json is the single contract for SKILL.md frontmatter. check_skill_structure
enforces a useful, NON-NOISY subset of it: required fields must be present and known fields
must be well-typed. Crucially it must NOT flag a skill merely for lacking an OPTIONAL field
(when_to_use/argument-hint/...) — those are coverage, not a structural error, so the existing
tree stays clean until then. `category`/`license`/`keywords`/`user-invocable` are RETIRED
harness fields (no consumer, dead weight in the listing budget) — the schema no longer
declares them, though `additionalProperties: true` still tolerates a stray one on disk.
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_skill_structure as css  # noqa: E402

_SCHEMA = _REPO / "harness" / "schemas" / "skill-schema.json"


def _make_skill(tmp_path, frontmatter: str, body: str = "# Body\n\nUse when needed.\n"):
    d = tmp_path / "askill"
    d.mkdir()
    (d / "SKILL.md").write_text("---\n%s\n---\n\n%s" % (frontmatter, body), encoding="utf-8")
    return str(d)


def _rules(result):
    return {f["rule"] for f in result["findings"]}


# ---- the schema file is the contract ---------------------------------------

def test_schema_file_is_valid_and_declares_contract():
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    assert set(schema.get("required", [])) >= {"name", "description"}
    props = schema.get("properties", {})
    for opt in ("when_to_use", "argument-hint", "allowed-tools",
                "paths", "disable-model-invocation", "injectable"):
        assert opt in props, "schema must declare optional field %r" % opt


def test_schema_no_longer_declares_retired_fields():
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    props = schema.get("properties", {})
    for retired in ("category", "license", "keywords", "user-invocable"):
        assert retired not in props, "retired field %r must not be in the schema" % retired


# ---- enforcement merged into check_skill_structure -------------------------

def test_flags_missing_required_description(tmp_path):
    res = css.check_skill(_make_skill(tmp_path, "name: hs:x"))
    assert "frontmatter-missing-required" in _rules(res)


def test_flags_bad_type_allowed_tools(tmp_path):
    res = css.check_skill(_make_skill(
        tmp_path, "name: hs:x\ndescription: Do a thing. Use when you need it done well here.\nallowed-tools: 5"))
    assert "frontmatter-bad-type" in _rules(res)


def test_clean_when_only_required_present(tmp_path):
    # name + description, no optional fields -> NO frontmatter finding (tree stays clean)
    res = css.check_skill(_make_skill(
        tmp_path, "name: hs:x\ndescription: Do a thing. Use when you need it done well here."))
    assert not any(r.startswith("frontmatter-") for r in _rules(res))


def test_accepts_full_frontmatter(tmp_path):
    fm = (
        "name: x\n"
        "description: Do a thing. Use when you need it done well here.\n"
        "when_to_use: Use when X happens.\n"
        "argument-hint: \"<path>\"\n"
        "allowed-tools: Bash, Read\n"
        "injectable: true\n"
        "paths: [a, b]\n"
        "disable-model-invocation: true\n"
        "metadata:\n  compliance-tier: workflow\n"
    )
    res = css.check_skill(_make_skill(tmp_path, fm))
    assert not any(r.startswith("frontmatter-") for r in _rules(res))
