"""The core-immutable floor is the 13 spine + the off-skill proxy trio — 16 total.

Binding ruling: the installer pins the 13-skill spine PLUS the three skills that make
every OTHER skill reachable while off — `use` (the proxy), `find-skills` (routing), and
`cleanup` (orphan sweep). Those three are immutable for the same reason the spine is: if
they could be disabled, the machinery that reaches, routes to, and sweeps disabled skills
would strand itself. Every OTHER handoff target stays default-on-but-disableable. This
test locks the count + membership so a later edit cannot quietly widen or shrink the set.
"""

from pathlib import Path

from harness.scripts import skill_deps

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPS_PATH = REPO_ROOT / "harness" / "data" / "skill-deps.yaml"

SPINE = {
    "plan", "cook", "test", "ship", "fix", "debug", "code-review",
    "review-pr", "git", "scout", "understand", "setup", "triage",
}

# The off-skill proxy floor pinned beside the spine (interview #22).
FLOOR_EXTRA = {"use", "find-skills", "cleanup"}
FLOOR = SPINE | FLOOR_EXTRA


def test_core_immutable_is_exactly_the_floor():
    immutable = skill_deps.core_immutable(DEPS_PATH)
    assert set(immutable) == FLOOR


def test_no_ordinary_handoff_target_leaked_into_core_immutable():
    immutable = set(skill_deps.core_immutable(DEPS_PATH))
    # Ordinary handoff targets like afk / repomix / brainstorm must NOT be immutable —
    # only the spine + proxy floor are.
    for not_immutable in ("afk", "repomix", "brainstorm", "bakeoff", "voice"):
        assert not_immutable not in immutable


def test_core_immutable_equals_map_spine_plus_proxy_floor():
    # The floor is named in independent sources: this file's FLOOR literal and
    # skill-deps `core_immutable`. The spine half is also in the decomposition map's
    # `hs` group; the proxy trio is the deliberate delta on top. Pinning the two
    # machine sources to each other catches a spine skill added to the map but not to
    # core_immutable (or vice versa) instead of letting them silently diverge.
    from harness.scripts import migrate_decomposition as md
    m = md.load_map(REPO_ROOT / "harness" / "data" / "decomposition-map.yaml")
    assert set(skill_deps.core_immutable(DEPS_PATH)) == set(md.spine_skills(m)) | FLOOR_EXTRA
