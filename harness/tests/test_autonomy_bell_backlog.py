"""test_autonomy_bell_backlog.py — the bell's run-scoped backlog signal.

The bell's empty/found REPORT stays model-driven (F3); this adds a DETERMINISTIC,
run-scoped query helper the protocol consults as evidence. C2: the query MUST be
scoped to the run's source_ref — a GLOBAL open backlog item must never pin the
bell to `found` (which would make a loop unable to STOP). No run tag → the
backlog abstains (returns None) and the bell falls back to its other scope
evidence. backlog.yaml absent (pre-migration) → BACKLOG.md content scan, no crash.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import autonomy_bell as ab  # noqa: E402
import backlog_register as br  # noqa: E402


def test_bell_found_when_run_tagged_open_record_exists(tmp_path):
    br.add(tmp_path, text="run work", type="bug", priority="P2",
           source_ref="run-42")
    assert ab.backlog_signal(tmp_path, source_ref="run-42") == "found"


def test_bell_empty_when_run_records_done_despite_other_runs_open(tmp_path):
    # this run's only item, plus an UNRELATED open item from another run
    br.add(tmp_path, text="mine", type="bug", priority="P2", source_ref="run-A")
    br.add(tmp_path, text="theirs", type="bug", priority="P2",
           source_ref="run-B")
    br.done(tmp_path, "BL-001")  # this run's item closed
    # run-B is still open globally, but the bell scoped to run-A must say empty
    assert ab.backlog_signal(tmp_path, source_ref="run-A") == "empty"


def test_bell_backlog_abstains_without_run_tag(tmp_path):
    br.add(tmp_path, text="global open", type="bug", priority="P2")
    # no source_ref → abstain (None); a global open item must NOT force found
    assert ab.backlog_signal(tmp_path, source_ref=None) is None
    assert ab.backlog_signal(tmp_path, source_ref="") is None


def test_bell_falls_back_when_backlog_yaml_absent(tmp_path):
    # no docs/backlog.yaml; a BACKLOG.md prose file exists (pre-migration)
    (tmp_path / "BACKLOG.md").write_text(
        "# Backlog\n\n- something about run-77 still open\n", encoding="utf-8")
    # must not crash; the run tag appears in prose → found
    assert ab.backlog_signal(tmp_path, source_ref="run-77") == "found"
    # a tag not present → empty (still no crash)
    assert ab.backlog_signal(tmp_path, source_ref="run-absent") == "empty"


def test_bell_neither_source_returns_none(tmp_path):
    # neither backlog.yaml nor BACKLOG.md → abstain, no crash
    assert ab.backlog_signal(tmp_path, source_ref="run-1") is None


def test_bell_query_path_no_subprocess(tmp_path, monkeypatch):
    # the helper imports the query fn — it must not shell out
    br.add(tmp_path, text="x", type="bug", priority="P2", source_ref="run-9")

    def _boom(*a, **k):  # any subprocess use is a failure
        raise AssertionError("backlog_signal must not spawn a subprocess")

    monkeypatch.setattr("subprocess.run", _boom, raising=False)
    monkeypatch.setattr("subprocess.Popen", _boom, raising=False)
    assert ab.backlog_signal(tmp_path, source_ref="run-9") == "found"
