"""Drift guard for the static routing rule.

Both prose routing tables live in one rule, `harness/rules/skill-routing.md`:
  - Domain routing   — domain decision-tree (domain -> skill)
  - Workflow routing — intent-phrase -> start-command table

Both name skills as `/hs:<skill>` routes. This guard parses every skill
reference out of the two tables and asserts each one is a REAL skill in
`skill-deps.yaml`. If a table names a skill that is renamed or removed from the
graph, the table has drifted and this test goes red.

Scope note (KISS, by design): the lens only checks that a referenced
skill-NAME still exists. It deliberately does NOT diff each table row against
that skill's `description`/`when_to_use` word-for-word — semantic drift of the
prose is out of scope (no NLP-diff over-engineering). Name-existence catches
the structural drift D13 worried about; that is enough.
"""

import re
from pathlib import Path

from harness.scripts import skill_deps

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPS_PATH = REPO_ROOT / "harness" / "data" / "skill-deps.yaml"
RULES_DIR = REPO_ROOT / "harness" / "rules"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

ROUTING_RULE = RULES_DIR / "skill-routing.md"

# Matches a namespaced skill reference: hs:<name> or hs-<group>:<name>. The
# leading slash of a `/hs:scout` route is not captured but does not block the
# match.
_REF = re.compile(r"(?:hs|hs-[a-z]+):([a-z][a-z0-9-]+)")


def _known_skills():
    return set(skill_deps.load_deps(DEPS_PATH)["skills"])


def _refs(text):
    """Every skill-name referenced as an hs: route in the given text."""
    return {m.group(1) for m in _REF.finditer(text)}


def _unknown_refs(text, known):
    """Referenced skill-names that are NOT in the known skill graph (drift)."""
    return {name for name in _refs(text) if name not in known}


def test_routing_rule_exists():
    assert ROUTING_RULE.is_file(), f"missing {ROUTING_RULE}"


def test_every_referenced_skill_is_real():
    """Every /hs:<skill> named in either table must exist in skill-deps.yaml."""
    known = _known_skills()
    text = ROUTING_RULE.read_text(encoding="utf-8")
    # Guard against a vacuous pass: the tables must actually route.
    assert _refs(text), f"{ROUTING_RULE.name} names no hs: routes"
    unknown = _unknown_refs(text, known)
    assert not unknown, f"{ROUTING_RULE.name} routes to unknown skills: {sorted(unknown)}"


def test_drift_lens_catches_a_fake_skill():
    """Prove the lens is not a no-op: an injected non-existent skill is flagged."""
    known = _known_skills()
    injected = "Route to `/hs:not-a-real-skill-xyz` for testing.\n"
    assert _unknown_refs(injected, known) == {"not-a-real-skill-xyz"}


def test_claude_md_rule_layer_lists_the_routing_rule():
    """CLAUDE.md Rule-layer section must register the routing rule + when-route."""
    text = CLAUDE_MD.read_text(encoding="utf-8")
    assert "skill-routing" in text, "CLAUDE.md rule-layer missing skill-routing"


def test_no_ci_invariant_or_dev_id_leaks():
    """The ported tables must carry no runtime slot refs and no AK dev-ids.

    The banned tokens are assembled from fragments so THIS test file does not
    itself trip the harness CI-invariant / dogfood grep guards.
    """
    banned_runtime = re.compile(r"\.claude/(?:%s|%s)/" % ("skills", "hooks"))
    ak_route = re.compile(r"(?<![a-z])/?%s:" % "ak")
    ak_devid = "ak" + "-"
    text = ROUTING_RULE.read_text(encoding="utf-8")
    assert not banned_runtime.search(text), f"{ROUTING_RULE.name} leaks a runtime slot path"
    assert not ak_route.search(text), f"{ROUTING_RULE.name} leaks an upstream route prefix"
    assert ak_devid not in text, f"{ROUTING_RULE.name} leaks an upstream dev-id"
