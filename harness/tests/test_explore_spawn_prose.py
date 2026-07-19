"""Lint: every real Explore spawn-site in the skill prose pins model:"haiku".

Explore inherits the session model (Opus post-CC-2.1.198); a search-spawn that does not
pin haiku silently bills Opus for file-finding. This test hard-lists the genuine
spawn-instruction sites (not descriptive prose or Anthropic-doc examples) and asserts each
carries a model:"haiku" directive. It does NOT assert on the "leave-alone" files
(task-management schema metadata, team agent-teams docs) — flagging those would push the
directive into the wrong context.
"""
import re
from pathlib import Path

_HARNESS = Path(__file__).resolve().parents[1]

# Files whose Explore spawn-site MUST carry model:"haiku". Relative to harness/.
_SPAWN_SITES = [
    "plugins/hs/skills/scout/references/internal-scouting.md",
    "plugins/hs/skills/scout/SKILL.md",
    "plugins/hs/skills/fix/references/workflow-standard.md",
    "plugins/hs/skills/fix/references/workflow-deep.md",
    "plugins/hs/skills/fix/references/skill-activation-matrix.md",
    "plugins/hs/skills/fix/references/workflow-logs.md",
    # understand/references/chain-orchestration.md is NOT here: it delegates the scout
    # fan-out to hs:scout (whose own SKILL.md + internal-scouting.md ARE listed and carry
    # the Explore+haiku spawn) rather than re-implementing an Explore spawn-site itself —
    # so it has no genuine Explore spawn to pin. Re-add it only if it spawns Explore directly.
    "plugins/hs/skills/docs/references/update-workflow.md",
]

# Matches model:"haiku" | model: haiku | model='haiku' | model haiku (case-insensitive).
_MODEL_HAIKU = re.compile(r"model['\"]?\s*[:=]?\s*['\"]?\s*haiku", re.IGNORECASE)


def test_spawn_sites_pin_haiku():
    missing = []
    for rel in _SPAWN_SITES:
        p = _HARNESS / rel
        assert p.is_file(), "spawn-site file vanished: %s" % rel
        text = p.read_text(encoding="utf-8")
        if not _MODEL_HAIKU.search(text):
            missing.append(rel)
    assert not missing, "spawn-sites missing model:\"haiku\" directive: %s" % missing


def test_spawn_sites_still_reference_explore():
    # Guard the LESSONS rule "a skill-ref must not be deleted": the directive is ADDED,
    # never at the cost of removing the Explore reference it annotates.
    for rel in _SPAWN_SITES:
        text = (_HARNESS / rel).read_text(encoding="utf-8")
        assert "Explore" in text, "Explore reference lost in %s" % rel
