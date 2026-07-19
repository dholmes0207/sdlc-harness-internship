"""test_team_context_inject_hook.py — the SubagentStart team-context injector is
RETIRED under personal-first: there is no roster to announce to a subagent, and it
was a roster-config consumer that must not survive. This file now pins its absence
(hook file gone + no registration entry)."""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HOOK = _REPO / "harness" / "hooks" / "team_context_inject.py"
_REG = _REPO / "harness" / "install" / "hooks-registration.yaml"


def test_hook_file_removed():
    assert not _HOOK.exists(), "team_context_inject.py must be removed (personal-first)"


def test_no_registration_entry():
    reg = _REG.read_text(encoding="utf-8")
    assert "team_context_inject" not in reg, "team_context_inject must be unregistered"
