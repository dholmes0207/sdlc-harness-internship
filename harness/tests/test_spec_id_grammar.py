"""hs:spec — parent-scoped ID grammar SSOT (id_grammar.py)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Literal path below keeps the stashed-skill collect_ignore coupling working:
# harness/plugins/hs/skills/spec/scripts
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_mods = load_skill_scripts(_SPEC_SCRIPTS, ["id_grammar"])
id_grammar = _mods["id_grammar"]


# ---------------------------------------------------------------------------
# SSOT drift guard: the slug character class must be defined ONCE (SLUG_RE)
# and every other pattern (prd/epic/story/COMP) must COMPOSE from it instead
# of re-encoding the literal — otherwise valid_slug() and is_valid_id() can
# silently drift apart from each other.
# ---------------------------------------------------------------------------

def test_slug_class_defined_once_in_id_grammar_source():
    src = (_SPEC_SCRIPTS / "id_grammar.py").read_text(encoding="utf-8")
    # Count only actual re.compile(...) call sites (the module docstring's
    # prose grammar table legitimately mentions the shape once too, but that
    # is documentation, not a second regex definition).
    compile_lines = [ln for ln in src.splitlines() if "re.compile(" in ln]
    occurrences = sum(ln.count("[A-Z][A-Z0-9-]{0,15}") for ln in compile_lines)
    assert occurrences == 1, (
        f"the uppercase-slug character class must be compiled once (in "
        f"SLUG_RE) and every other pattern composed from it; found "
        f"{occurrences} re.compile(...) occurrences — re-encoded elsewhere?"
    )


def test_prd_epic_story_comp_patterns_compose_from_slug_re():
    # The bare slug body (no anchors), as the module derives it. Reading it off
    # id_grammar keeps this in step with whichever end-anchor the patterns use.
    slug_body = id_grammar._SLUG_BODY
    for pattern in (
        id_grammar.ID_PATTERN_BY_TYPE["prd"],
        id_grammar.ID_PATTERN_BY_TYPE["epic"],
        id_grammar.ID_PATTERN_BY_TYPE["story"],
        id_grammar.COMP_ID_PATTERN,
    ):
        assert slug_body in pattern.pattern


def test_comp_id_pattern_not_re_encoded_in_check_consistency_competition():
    src = (_SPEC_SCRIPTS / "check_consistency_competition.py").read_text(encoding="utf-8")
    assert "COMP-[A-Z]" not in src, "COMP_ID_PATTERN must be imported from id_grammar, not re-encoded"
    assert "from id_grammar import" in src and "COMP_ID_PATTERN" in src


@pytest.mark.parametrize("node_id,node_type", [
    ("PRODUCT", "product"),
    ("VISION", "vision"),
    ("BRD-G1", "goal"),
    ("BRD-G42", "goal"),
    ("PRD-AUTH", "prd"),
    ("PRD-BILLING", "prd"),
    ("PRD-AUTH-E1", "epic"),
    ("PRD-AUTH-E2", "epic"),
    ("PRD-AUTH-E1-S1", "story"),
    ("PRD-ONBOARDING-E3-S12", "story"),
])
def test_valid_ids_match_their_type(node_id, node_type):
    assert id_grammar.is_valid_id(node_id, node_type) is True


@pytest.mark.parametrize("node_id,node_type", [
    ("prd-auth", "prd"),                       # lowercase slug
    ("PRD-THISSLUGISWAYTOOLONG16", "prd"),     # slug > 16 chars
    ("PRD AUTH", "prd"),                        # space
    ("PRD-AUTH-E1-S1", "epic"),                # story id given as epic
    ("BRD-GX", "goal"),                         # non-numeric goal index
    ("PRD-AUTH-EA", "epic"),                    # non-numeric epic index
])
def test_invalid_ids_rejected(node_id, node_type):
    assert id_grammar.is_valid_id(node_id, node_type) is False


def test_slug_rules():
    assert id_grammar.valid_slug("AUTH") is True
    assert id_grammar.valid_slug("A") is True
    assert id_grammar.valid_slug("AUTH-2FA") is True
    assert id_grammar.valid_slug("A" * 16) is True
    assert id_grammar.valid_slug("A" * 17) is False
    assert id_grammar.valid_slug("auth") is False       # must start uppercase
    assert id_grammar.valid_slug("2FA") is False        # must start letter
    assert id_grammar.valid_slug("AU TH") is False       # no space


@pytest.mark.parametrize("dec_id", ["DEC-1", "DEC-2", "DEC-99"])
def test_dec_ids_parent_free_monotonic(dec_id):
    assert id_grammar.DEC_ID_PATTERN.match(dec_id) is not None


@pytest.mark.parametrize("out_id", ["OUT-1", "OUT-2", "OUT-42"])
def test_out_ids_parent_free_monotonic(out_id):
    assert id_grammar.OUT_ID_PATTERN.match(out_id) is not None


@pytest.mark.parametrize("bad", ["DEC-", "DEC-X", "DECISION-1", "dec-1"])
def test_dec_ids_reject_malformed(bad):
    assert id_grammar.DEC_ID_PATTERN.match(bad) is None


def test_id_type_infers_kind():
    assert id_grammar.id_type("PRODUCT") == "product"
    assert id_grammar.id_type("VISION") == "vision"
    assert id_grammar.id_type("BRD-G1") == "goal"
    assert id_grammar.id_type("PRD-AUTH") == "prd"
    assert id_grammar.id_type("PRD-AUTH-E1") == "epic"
    assert id_grammar.id_type("PRD-AUTH-E1-S1") == "story"
    assert id_grammar.id_type("garbage id") is None
    assert id_grammar.id_type("prd-auth") is None


def test_competitor_grammar():
    assert id_grammar.COMP_ID_PATTERN.match("COMP-SHOPIFY") is not None
    assert id_grammar.COMP_ID_PATTERN.match("COMP-BIGCARTEL") is not None
    assert id_grammar.COMP_ID_PATTERN.match("comp-shopify") is None


def test_pattern_table_is_the_shared_home():
    # spec_graph must consume these patterns (DRY) rather than re-encode them.
    assert set(id_grammar.ID_PATTERN_BY_TYPE) >= {
        "product", "vision", "goal", "prd", "epic", "story"}


# ---------------------------------------------------------------------------
# Anchor discipline: every id/slug pattern must anchor its END with `\Z`, not
# `$`. Python's `$` also matches immediately BEFORE a single trailing newline,
# so a YAML literal-block scalar (`id: |\n  PRD-AUTH`) parsed through the SSOT
# reader to `'PRD-AUTH\n'` would validate as a well-formed id — then silently
# fail every downstream dict-keyed / equality lookup (`'PRD-AUTH\n' !=
# 'PRD-AUTH'`), surfacing as an orphaned node or a dangling serves link rather
# than the `invalid_id` the grammar gate exists to raise.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("node_id,node_type", [
    ("PRD-AUTH\n", "prd"),
    ("PRD-AUTH-E1\n", "epic"),
    ("PRD-AUTH-E1-S1\n", "story"),
    ("BRD-G1\n", "goal"),
    ("PRODUCT\n", "product"),
    ("VISION\n", "vision"),
])
def test_trailing_newline_id_is_rejected(node_id, node_type):
    assert id_grammar.is_valid_id(node_id, node_type) is False


def test_trailing_newline_id_type_is_none():
    assert id_grammar.id_type("PRD-AUTH\n") is None
    assert id_grammar.id_type("PRD-AUTH-E1\n") is None
    assert id_grammar.id_type("PRD-AUTH-E1-S1\n") is None


def test_trailing_newline_slug_is_rejected():
    assert id_grammar.valid_slug("AUTH\n") is False


def test_trailing_newline_dec_out_comp_rejected():
    assert id_grammar.DEC_ID_PATTERN.match("DEC-1\n") is None
    assert id_grammar.OUT_ID_PATTERN.match("OUT-1\n") is None
    assert id_grammar.COMP_ID_PATTERN.match("COMP-SHOPIFY\n") is None
