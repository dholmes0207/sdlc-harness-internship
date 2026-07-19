"""The cook SKILL.md must not misstate this repo's plans/ tracking.

Ground truth (.gitignore): plans/ is TRACKED — only plans/reports/ is untracked scratch.
An earlier SKILL revision claimed `.gitignore plans/**/*` hides plan dirs so artifacts
"do NOT auto-commit". That is false here and misled a cook run into thinking verification/
review-decision artifacts could not be committed. Pin the correction against the real
.gitignore so the false mechanism cannot be reintroduced.
"""
from pathlib import Path
import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_COOK = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "skills" / "cook"



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _body(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[-1]
    return text


def test_cook_skill_does_not_misstate_plans_gitignore():
    gitignore = (_REPO / ".gitignore").read_text(encoding="utf-8")
    # Reality: reports/ is the only plans/ path ignored; no blanket plans/ ignore.
    assert "plans/reports/" in gitignore
    assert "\nplans/**" not in gitignore and "\nplans/*\n" not in gitignore, \
        "test premise broke: plans/ is now blanket-ignored — update the SKILL wording too"
    body = _body(_COOK / "SKILL.md").lower()
    assert "gitignore plans" not in body, \
        "cook SKILL wrongly claims plans/ is gitignored; repo tracks plans/ (only reports/ scratch)"
    assert "do not auto-commit" not in body, \
        "cook SKILL wrongly claims plan artifacts do not auto-commit"
