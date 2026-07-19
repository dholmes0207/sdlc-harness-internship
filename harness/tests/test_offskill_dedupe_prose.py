"""test_offskill_dedupe_prose.py — Option-B dedupe prose contract.

find-skills OWNS off-skill discovery (list + purpose-route + [OFF] tag); hs:use ONLY runs
an off skill from stash + reports + emits demand. These are prose contracts (the agent
reads SKILL.md at runtime), so we assert the load-bearing MUST/NEVER sentences are present
and that hs:use does NOT re-implement the discovery it must delegate — the same class of
drift guard as test_handoff_deps_drift.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_USE = _ROOT / "harness/plugins/hs/skills/use/SKILL.md"
_MECH = _ROOT / "harness/plugins/hs/skills/use/references/mechanisms.md"
_FIND = _ROOT / "harness/plugins/hs/skills/find-skills/SKILL.md"
_RULE = _ROOT / "harness/rules/disabled-group-handling.md"
_CLAUDE = _ROOT / "CLAUDE.md"


def _text(p):
    return p.read_text(encoding="utf-8")


def test_use_delegates_discovery_not_duplicate():
    t = _text(_USE)
    low = t.lower()
    # MUST-delegate discovery to find-skills
    assert "find-skills" in low
    assert "delegate" in low and "discovery" in low
    # a strong MUST sentence tying discovery/list/route to find-skills
    assert re.search(r"MUST.{0,80}(delegate|find-skills)", t), \
        "use/SKILL.md needs a MUST-delegate-discovery sentence"
    # hs:use must NOT re-own catalog listing: no '--list' mechanism table row of its own
    assert "hs:use --list" not in low
    # keep the run-from-stash + emit-demand contract
    assert "emit_disabled_demand" in t
    assert "proxy_run" in t


def test_use_keeps_must_never_load_bearing():
    t = _text(_USE)
    assert "--status" in t and "NEVER" in t          # resolve state first
    assert re.search(r"[Ll]ive.{0,40}delegate", t)    # live → delegate /hs:<name>
    assert "--chain" in t                             # off deps load order


def test_mechanisms_drops_list_route_reimpl():
    t = _text(_MECH)
    low = t.lower()
    assert "find-skills" in low                       # delegates discovery
    assert "emit_disabled_demand" in t                # keeps the emit step


def test_offmatch_must_tag_and_route_hs_use():
    t = _text(_FIND)
    assert "[OFF" in t                                # tags off matches
    assert "MUST" in t
    assert "/hs:use" in t
    # NEVER route an off skill via a raw /hs:<name>
    assert re.search(r"NEVER.{0,80}(raw|/hs:<name>)", t), \
        "find-skills must forbid raw /hs:<name> for an off skill"


def test_rule_and_claudemd_mention_hs_use():
    rule = _text(_RULE)
    assert "hs:use" in rule                           # rule now names the primary path
    assert "disabled-skills/<skill>" in rule          # read-inline path corrected to stash
    assert "hs/skills/<skill>" not in rule            # stale wrong (live-dir) path removed
    claude = _text(_CLAUDE)
    low = claude.lower()
    assert "find-skills --list" in low or "find-skills" in low
    assert "hs:use" in claude                          # always-on discovery line
    assert "[off]" in low                              # mentions off skills can exist
