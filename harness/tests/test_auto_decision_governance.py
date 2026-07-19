"""test_auto_decision_governance.py — the ledger subsystem is enabled + governed.

The nudge is enabled in harness-hooks.yaml (it was already registered in the Stop dispatch
lane), a harness DEC records the subsystem, the glossary carries the term, the architecture
doc names it, and the toggle config documents that the ledger is INDEPENDENT of
HARNESS_AUTONOMY. Content-based asserts (not a hardcoded DEC number) so an allocator-assigned
id does not make the gate brittle.
"""
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
_HARNESS = _REPO / "harness"


def _yaml(p):
    return yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}


def test_nudge_registered_in_dispatch():
    data = _yaml(_HARNESS / "data" / "hook-dispatch.yaml")
    stop = data.get("groups", {}).get("Stop") or data.get("Stop") or []
    names = [e.get("name") for e in stop if isinstance(e, dict)]
    assert "auto_decision_review_nudge" in names
    entry = next(e for e in stop if e.get("name") == "auto_decision_review_nudge")
    assert entry.get("entry") == "handle_stop"
    assert entry.get("class") == "nudge"


def test_nudge_enabled_in_hooks_yaml():
    data = _yaml(_HARNESS / "data" / "harness-hooks.yaml")
    hooks = data.get("hooks", data)
    entry = hooks.get("auto_decision_review_nudge")
    assert isinstance(entry, dict) and entry.get("enabled") is True


def test_dec_present():
    text = (_REPO / "docs" / "decisions.yaml").read_text(encoding="utf-8").lower()
    assert "auto-decision ledger" in text


def test_glossary_term_present():
    text = (_REPO / "docs" / "glossary.yaml").read_text(encoding="utf-8").lower()
    assert "auto-decision ledger" in text


def test_arch_doc_mentions_ledger():
    text = (_REPO / "docs" / "system-architecture.md").read_text(encoding="utf-8").lower()
    assert "auto-decision ledger" in text


def test_autonomy_independence_documented():
    text = (_HARNESS / "data" / "auto-decision.yaml").read_text(encoding="utf-8")
    assert "HARNESS_AUTONOMY" in text
