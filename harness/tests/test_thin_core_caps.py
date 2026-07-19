"""test_thin_core_caps.py — the durable per-skill body-cap override mechanism.

Distinct from the grandfather ledger (`thin-core-grandfather.yaml`), which holds
TEMPORARY debt that re-teeths under `--strict` and should shrink to empty: this
mechanism (`thin-core-caps.yaml`) grants a PERMANENT, still-bounded higher body cap
to a named skill whose always-read directive set legitimately exceeds the general
thin-core cap (the two spine orchestrators, cook + plan, carry the densest mandatory
gate + delegate-checkpoint prose).

The exemption is deliberately NOT a "waive the cap" escape. This guard keeps it honest
and controlled — no blind ballooning:

  - an absolute ceiling bounds EVERY override (a file entry above it is clamped),
  - every override sits ABOVE the general cap (an override at/below it is pointless),
  - every override carries a real REASON (raising a cap is a deliberate, visible act),
  - an override only survives for a skill that ACTUALLY needs it (a skill that shrinks
    back under the general cap must drop its override — this test makes that visible),
  - an exempted skill's body still stays within ITS granted (higher) cap.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_skill_structure as css  # noqa: E402
import skill_frontmatter  # noqa: E402

_SKILLS = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills"


def _overrides() -> dict:
    return css.load_skill_cap_overrides()


def test_ceiling_is_bounded_headroom_above_general_cap():
    """The exemption ceiling is a real, bounded headroom above the general cap — the
    mechanism grants MORE budget, never UNLIMITED budget."""
    assert css.MAX_SKILL_CHARS < css.EXEMPTION_HARD_CEILING
    # headroom stays modest: the ceiling is not a licence to double the thin-core budget.
    assert css.EXEMPTION_HARD_CEILING <= 2 * css.MAX_SKILL_CHARS


def test_every_override_is_bounded_and_reasoned():
    """No blind ballooning: each override sits in (general_cap, its_ceiling] and carries a
    non-trivial reason so a cap-raise is always a deliberate, documented act the reader
    can see. A named clamp-exemption is bounded by its OWN (higher) ceiling; every other
    skill by the general EXEMPTION_HARD_CEILING."""
    overrides = _overrides()
    assert overrides, "expected at least one documented override (cook/plan)"
    for name, spec in overrides.items():
        body_max = spec.get("body_max")
        assert isinstance(body_max, int), "%s override body_max must be an int" % name
        ceiling = css.exemption_ceiling(name)
        assert css.MAX_SKILL_CHARS < body_max <= ceiling, (
            "%s override %r must fall within (%d, %d]"
            % (name, body_max, css.MAX_SKILL_CHARS, ceiling))
        reason = (spec.get("reason") or "").strip()
        assert len(reason) >= 40, "%s override needs a real reason (got %r)" % (name, reason)


def test_named_clamp_exemptions_are_bounded_and_two_key():
    """A named clamp-exemption gets a HIGHER ceiling than the general one, but it is still
    bounded (never unlimited) and two-key: it must also carry a reasoned data-file override,
    so neither the code allowlist nor the data file can grant the raise alone."""
    overrides = _overrides()
    for name, ceiling in css._UNCLAMPED_EXEMPTIONS.items():
        assert css.EXEMPTION_HARD_CEILING < ceiling <= 2 * css.MAX_SKILL_CHARS, (
            "%s clamp-exemption ceiling %d must sit in (%d, %d]"
            % (name, ceiling, css.EXEMPTION_HARD_CEILING, 2 * css.MAX_SKILL_CHARS))
        assert name in overrides, (
            "%s is code-allowlisted but has no thin-core-caps.yaml entry (two-key breach)" % name)


def test_clamp_defends_against_an_absurd_file_entry():
    """A data-file entry above the ceiling is clamped — editing thin-core-caps.yaml to an
    absurd value cannot grant unbounded budget. Tests the PRODUCTION clamp on the real path
    (`skill_body_cap` -> `_clamp_body_max`), name-aware, for both ceiling tiers."""
    # a non-exempt skill: absurd value clamps at the general ceiling
    assert css._clamp_body_max(999999, "some-unexempted-skill") == css.EXEMPTION_HARD_CEILING
    assert css._clamp_body_max(css.MAX_SKILL_CHARS + 1000, "some-unexempted-skill") == \
        css.MAX_SKILL_CHARS + 1000
    # a named clamp-exemption: absurd value clamps at ITS higher ceiling, not the global one
    for nm, ceil in css._UNCLAMPED_EXEMPTIONS.items():
        assert css._clamp_body_max(999999, nm) == ceil
        assert ceil > css.EXEMPTION_HARD_CEILING


def test_unlisted_skill_uses_the_general_cap():
    """A skill with no override falls back to the general thin-core cap — the exemption
    never leaks to skills that did not opt in."""
    assert css.skill_body_cap("a-skill-with-no-override") == css.MAX_SKILL_CHARS


def test_override_is_necessary_skill_actually_exceeds_general_cap():
    """An override must be NEEDED: the skill's real body exceeds the general cap. A skill
    that shrinks back under the cap has a dead override that must be removed."""
    for name in _overrides():
        body = skill_frontmatter.body((_SKILLS / name / "SKILL.md").read_text(encoding="utf-8"))
        assert len(body) > css.MAX_SKILL_CHARS, (
            "%s carries an override but its body %d is within the general cap %d — drop the override"
            % (name, len(body), css.MAX_SKILL_CHARS))


def test_exempted_skill_body_stays_within_its_raised_cap():
    """The raised cap is still ENFORCED: each exempted skill's body stays within ITS
    granted (higher) cap — the exemption is a different limit, not the absence of one."""
    for name in _overrides():
        body = skill_frontmatter.body((_SKILLS / name / "SKILL.md").read_text(encoding="utf-8"))
        cap = css.skill_body_cap(name)
        assert len(body) <= cap, "%s body %d exceeds even its raised cap %d" % (name, len(body), cap)
