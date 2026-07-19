"""Class-4 route wording for understand / discover (dev-repo only).

understand and discover chain sub-skills that self-route their own fan-out
(scout / research / brainstorm), so they must NOT double-route — they stay
see-also-honest, NOT in the D7 enforce set. But each must state the explicit
CONDITION: if it spawns its OWN extra fan-out beyond calling a sub-skill, it
routes through hs:workflow-orchestrate at that point.

The enforce-set membership is asserted against the REAL ENFORCE_ANCHORS map (not
a local copy) so that adding understand/discover to the map later trips this test
(M3 — no tautology).
"""

import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_workflow_consumers import ENFORCE_ANCHORS  # noqa: E402

_SKILLS = _TESTS_DIR.parents[0] / "plugins" / "hs" / "skills"


def _body(name):
    path = _SKILLS / name / "SKILL.md"
    assert path.is_file(), f"SKILL.md missing for {name}: {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.dev_repo
def test_understand_states_conditional_route():
    body = _body("understand")
    assert "hs:scout" in body, "understand must delegate its parallel exploration to hs:scout"
    assert ("its OWN" in body or "its own" in body.lower()), (
        "understand must state the conditional (only its OWN extra fan-out routes)"
    )
    assert "hs:workflow-orchestrate" in body


@pytest.mark.dev_repo
def test_discover_states_conditional_route():
    body = _body("discover")
    assert "self-route" in body, "discover must say its sub-skills self-route"
    assert "ONLY if" in body, "discover must state the ONLY-if condition for routing"
    assert "hs:workflow-orchestrate" in body


@pytest.mark.dev_repo
def test_class4_not_in_enforce_anchors():
    # Imported from the real invariant — not a local copy (M3 anti-tautology).
    assert "understand" not in ENFORCE_ANCHORS, (
        "understand is see-also-honest, it must NOT join the D7 enforce set"
    )
    assert "discover" not in ENFORCE_ANCHORS, (
        "discover is see-also-honest, it must NOT join the D7 enforce set"
    )
