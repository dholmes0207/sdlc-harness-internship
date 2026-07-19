#!/usr/bin/env python3
"""P2 — the harness's own (layer-b) review rules migrated to a folder.

The five plan-2.5 dogfood rules move out of the single root standards.user.yaml
into docs/standards/harness-self.std.yaml (one consolidated file), and a sixth
rule (fail-closed-posture) is added. A seventh rule (handoff-deps-link) keeps the
skill-deps graph from drifting from SKILL.md routes. An eighth rule
(investigate-cc-behavior) grounds any work that wraps Claude Code's own behavior
in a primary source (the binary / the claude-code-guide agent) rather than memory.
A ninth rule (no-dev-prefs-in-shipped-data) keeps personal/dev config out of the
shipped harness/data + harness-hooks.yaml (only team/protected/LESSONS are
pack-scrubbed). The root file stayed as a fallback until closeout, but the folder
now WINS precedence, so user_override.load on the real repo reads the folder rules.
"""

import sys
import pytest
from pathlib import Path

import yaml as _yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import mechanical_runner  # noqa: E402
import user_override  # noqa: E402

_FOLDER_FILE = _REPO_ROOT / "docs" / "standards" / "harness-self.std.yaml"
_ROOT_FILE = _REPO_ROOT / "standards.user.yaml"

_MIGRATED = {
    "USR-HARNESS-NO-CLAUDE-RUNTIME",
    "USR-HARNESS-HOOK-CLASS",
    "USR-HARNESS-STORE-APPEND-ONLY",
    "USR-HARNESS-CONFIG-DATA-SPLIT",
    "USR-HARNESS-NO-PLAN-IDS",
}
_SIXTH = "USR-HARNESS-FAIL-CLOSED-POSTURE"
_SEVENTH = "USR-HARNESS-HANDOFF-DEPS-LINK"
_EIGHTH = "USR-HARNESS-INVESTIGATE-CC-BEHAVIOR"
_NINTH = "USR-HARNESS-NO-DEV-PREFS-IN-SHIPPED-DATA"
# Later additions: the doc-freshness twin of the standards_drift nudge, the
# cross-cutting layer-boundary invariant, and two promoted lessons (config-field
# round-trip, producer-consumer wiring).
_LATER = {
    "USR-HARNESS-SAD-FRESHNESS",
    "USR-HARNESS-LAYER-BOUNDARY",
    "USR-HARNESS-CONFIG-FIELD-ROUNDTRIP",
    "USR-HARNESS-PRODUCER-CONSUMER-WIRING",
}
_EXPECTED = _MIGRATED | {_SIXTH, _SEVENTH, _EIGHTH, _NINTH} | _LATER


# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _by_id(overrides):
    return {o.get("rule_id"): o for o in overrides if isinstance(o, dict)}


# (a) the folder file exists and carries the layer-b rules.
def test_folder_file_has_expected_rules():
    assert _FOLDER_FILE.is_file(), "docs/standards/harness-self.std.yaml must exist"
    data = _yaml.safe_load(_FOLDER_FILE.read_text(encoding="utf-8"))
    ids = {o.get("rule_id") for o in data["overrides"]}
    assert ids == _EXPECTED, ids


# (b) closeout: the legacy root standards.user.yaml is gone and the folder is the
# sole source — the loader reads the six rules from the folder with no fallback.


# (c) the sixth rule (fail-closed-posture) is present and well-formed.
def test_sixth_rule_present():
    folder = _by_id(_yaml.safe_load(_FOLDER_FILE.read_text(encoding="utf-8"))["overrides"])
    assert _SIXTH in folder
    r = folder[_SIXTH]
    assert isinstance(r.get("reason"), str) and r["reason"].strip()
    assert r.get("scope")
    assert r.get("severity", "info") == "info"   # no floor yet (P4 promotes)


# (d) the folder now WINS: load() on the real repo reads the six folder rules.


# (e) the NO-CLAUDE-RUNTIME grep detector stays 0-FP on the real harness tree.
def test_no_claude_grep_zero_fp_after_migration():
    rules, _w = user_override.apply([], user_override.load(_REPO_ROOT))
    grep = [r for r in rules if isinstance(r.get("detector"), dict)
            and r["detector"].get("type") == "grep"]
    assert grep, "at least one grep rule expected from the folder"
    changed = [str(p.relative_to(_REPO_ROOT))
               for p in (_REPO_ROOT / "harness").rglob("*.py")]
    findings = mechanical_runner.run_grep_detectors(grep, changed, root=str(_REPO_ROOT))
    assert findings == [], findings[:10]
