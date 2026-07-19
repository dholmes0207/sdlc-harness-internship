"""_rule_ref_util.py — thin re-export of the shared rule-reference extractor
(K11 / red-team M2).

`validate_skill_crossrefs.py` is the CANONICAL owner of `extract_rule_refs` +
`RULE_REF_RE` (phase-3, CS-10): both `test_rule_route_existence.py` (F3) and the
phase-3 CS-10 extension of `validate_skill_crossrefs.py` now share the exact same
function object — a route-line hidden inside a code fence is invisible to BOTH
checks, and a route-line written as prose is visible to BOTH. Two divergent
extractors (one raw-substring, one fence-stripped) would let a fenced route-line
pass here in phase-1 and only fail CS-10 in phase-3 — proven by red-team M2.

Not a test module itself (no `test_` prefix), so pytest never collects it.
Dependency direction stays tests -> scripts (never scripts -> tests): this file
imports from `harness/scripts/validate_skill_crossrefs.py`, never the reverse.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from validate_skill_crossrefs import (  # noqa: E402,F401
    CODE_FENCE_RE,
    RULE_REF_RE,
    extract_rule_refs,
)
