"""Guard the retirement of the flat review-rules subsystem (P8).

The flat review-rules tree (review_rules.py, review_rules_manager.py,
schemas/review-rule.json, data/review-rules/) was removed once the std
operational tree became the single source. These guards keep it gone: no live
code imports the deleted module, and the deleted files do not reappear.
"""

import re
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
_GONE = [
    "scripts/review_rules.py",
    "scripts/review_rules_manager.py",
    "schemas/review-rule.json",
    "data/review-rules",
]

# match a real import of the legacy module, not a prose mention in a docstring
_IMPORT_RE = re.compile(r"^\s*(?:import\s+review_rules\b|from\s+review_rules\b)", re.M)


def test_legacy_files_are_gone():
    for rel in _GONE:
        assert not (_HARNESS / rel).exists(), f"{rel} should have been retired"


def test_no_live_import_of_review_rules():
    offenders = []
    for sub in ("scripts", "hooks", "plugins"):
        for p in (_HARNESS / sub).rglob("*.py"):
            if _IMPORT_RE.search(p.read_text(encoding="utf-8")):
                offenders.append(str(p.relative_to(_HARNESS)))
    assert not offenders, f"live import of retired review_rules: {offenders}"
