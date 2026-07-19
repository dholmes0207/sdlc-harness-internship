"""Absence contract for the removed advisory-delegate consent mechanism.

Test-first for a REMOVAL: these assertions define what must be gone once the
gate is torn out (core files, hook registration, skill embeds, config row,
ghost comments in the kept explore guards). Red while the mechanism is still
present; green after Phase 2 removes it.

Reads the repo tree directly (no import of any delegate module — they are
being deleted), so gated behind dev_repo: an installed copy ships none of
these artifacts and the absence is trivially satisfied there.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# The four core files that carry the mechanism (plan §Touchpoints).
_CORE_FILES = [
    "harness/hooks/delegate_consent_guard.py",
    "harness/scripts/delegate_consent.py",
    "harness/data/delegate-consent.yaml",
    "harness/rules/auto-delegate-consent.md",
]

# Files whose wiring registers or references the hook (plan §Touchpoints).
_WIRING_FILES = [
    ".claude/settings.json",
    "harness/data/harness-hooks.yaml",
    "harness/install/hooks-registration.yaml",
]

# The 13 skill docs that embed an advisory-delegate consent paragraph
# (copied verbatim from plan.md §Touchpoints — do NOT re-derive).
_EMBED_FILES = [
    "harness/plugins/hs/skills/code-review/references/risk-ceremony.md",
    "harness/plugins/hs/skills/brainstorm/SKILL.md",
    "harness/plugins/hs/skills/debug/SKILL.md",
    "harness/plugins/hs/skills/plan/references/research-phase.md",
    "harness/plugins/hs/skills/plan/references/red-team-gate.md",
    "harness/plugins/hs/skills/plan/references/archive-workflow.md",
    "harness/plugins/hs/skills/research/SKILL.md",
    "harness/plugins/hs/skills/cook/references/workflow-steps.md",
    "harness/plugins/hs/skills/fix/SKILL.md",
    "harness/plugins/hs/skills/security-scan/SKILL.md",
    "harness/plugins/hs/skills/critique/SKILL.md",
    "harness/plugins/hs/skills/discover/SKILL.md",
    "harness/plugins/hs/skills/team/SKILL.md",
]

# The trace strings the whole mechanism leaves behind.
_TRACES = ("delegate_consent", "auto-delegate-consent", "delegate-consent")

# Guards that are KEPT — they may not carry a ghost reference to the removed
# mechanism (comment scrub), but their own logic is untouched.
_KEPT_GUARDS = [
    "harness/hooks/model_guard.py",
    "harness/scripts/explore_override.py",
]


@pytest.mark.dev_repo
@pytest.mark.parametrize("rel", _CORE_FILES)
def test_core_files_absent(rel):
    assert not (_ROOT / rel).exists(), f"core mechanism file still present: {rel}"


@pytest.mark.dev_repo
@pytest.mark.parametrize("rel", _WIRING_FILES)
def test_no_hook_registration(rel):
    path = _ROOT / rel
    if not path.exists():
        # Install-output files (e.g. .claude/settings.json) are absent from a bare
        # git checkout / CI runner — an absent file trivially satisfies the absence
        # contract, so skip rather than red on FileNotFoundError.
        pytest.skip(f"{rel} not present in this checkout")
    text = path.read_text(encoding="utf-8")
    assert "delegate_consent_guard" not in text, (
        f"hook registration still references delegate_consent_guard: {rel}"
    )


@pytest.mark.dev_repo
@pytest.mark.parametrize("rel", _EMBED_FILES)
def test_no_skill_embed(rel):
    text = (_ROOT / rel).read_text(encoding="utf-8")
    hits = [t for t in _TRACES if t in text]
    assert not hits, f"skill still embeds consent trace {hits}: {rel}"


@pytest.mark.dev_repo
def test_no_config_reference_row():
    text = (_ROOT / "docs/harness/config-reference.md").read_text(encoding="utf-8")
    assert "HARNESS_DELEGATE_CONSENT" not in text, "config-reference still lists the env knob"
    assert "advisory-delegate consent" not in text, "config-reference still has the row"


@pytest.mark.dev_repo
@pytest.mark.parametrize("rel", _KEPT_GUARDS)
def test_kept_guards_no_ghost(rel):
    text = (_ROOT / rel).read_text(encoding="utf-8")
    assert "delegate_consent" not in text, f"kept guard still names removed mechanism: {rel}"
