"""test_compound_citation_check.py — re-verify the citations in an hs:compound proposal.

hs:compound grounds each self-improvement proposal in a citation: a BACKLOG item
(BACKLOG.md:NNN / BACKLOG:NNN) or a telemetry lens count
(lens:<name> <sub_key> "<chain>" count=N). A model can fabricate either. This checker
re-derives each citation from the primary source and FLAGS fabrications — but, like the
source's rejected-stays-visible rule, it never removes the proposal: a flagged proposal
is still shown, just marked unverified.

Two legs, independent: the BACKLOG-ID leg needs only the BACKLOG file; the lens-count
leg needs a lens index and is skipped when none is supplied.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import compound_citation_check as ccc  # noqa: E402


def _backlog(tmp_path, n_lines=250):
    p = tmp_path / "BACKLOG.md"
    p.write_text("\n".join("line %d" % i for i in range(1, n_lines + 1)) + "\n", encoding="utf-8")
    return p


# --- valid citations pass -----------------------------------------------------

def test_valid_citations_pass(tmp_path):
    backlog = _backlog(tmp_path, 250)
    text = (
        'Formalize the L3 store. Evidence: BACKLOG.md:198 and '
        'lens:workflow_chains pair "plan->cook" count=5.'
    )
    res = ccc.check_citations(text, backlog_path=str(backlog),
                              lens_index={"plan->cook": 5})
    assert res["verdict"] == "PASS"
    assert res["findings"] == []
    assert res["proposal_shown"] is True


# --- fabricated lens count is flagged but stays visible ------------------------

def test_fabricated_lens_count_flagged(tmp_path):
    backlog = _backlog(tmp_path, 250)
    text = 'Big win: lens:workflow_chains pair "plan->cook" count=99.'
    res = ccc.check_citations(text, backlog_path=str(backlog),
                              lens_index={"plan->cook": 5})
    rules = {f["rule"] for f in res["findings"]}
    assert "fabricated-lens-count" in rules
    # flagged, NOT removed — the proposal is still shown (rejected-stays-visible)
    assert res["verdict"] == "PASS_WITH_RISK"
    assert res["proposal_shown"] is True


def test_lens_count_within_tolerance_passes(tmp_path):
    # ±1 tolerance: a cited 6 against an actual 5 is not a fabrication.
    backlog = _backlog(tmp_path, 250)
    text = 'lens:workflow_chains pair "plan->cook" count=6.'
    res = ccc.check_citations(text, backlog_path=str(backlog),
                              lens_index={"plan->cook": 5})
    assert res["verdict"] == "PASS"


# --- missing BACKLOG id is flagged --------------------------------------------

def test_missing_backlog_id_flagged(tmp_path):
    backlog = _backlog(tmp_path, 250)  # only 250 lines
    text = "See BACKLOG.md:99999 for the rationale."
    res = ccc.check_citations(text, backlog_path=str(backlog), lens_index={})
    rules = {f["rule"] for f in res["findings"]}
    assert "fabricated-backlog-id" in rules
    assert res["verdict"] == "PASS_WITH_RISK"
    assert res["proposal_shown"] is True


def test_backlog_leg_runs_without_lens_index(tmp_path):
    # The BACKLOG-ID leg is independent: it verifies even when no lens index is given.
    backlog = _backlog(tmp_path, 10)
    text = "BACKLOG:200 cited."  # 200 > 10 lines => fabricated
    res = ccc.check_citations(text, backlog_path=str(backlog), lens_index=None)
    assert any(f["rule"] == "fabricated-backlog-id" for f in res["findings"])


def test_lens_leg_skipped_when_no_index(tmp_path):
    # A lens citation present but no index supplied => the lens leg is skipped, not
    # treated as a fabrication (the index is what's missing, not the citation).
    backlog = _backlog(tmp_path, 250)
    text = 'lens:workflow_chains pair "plan->cook" count=99.'
    res = ccc.check_citations(text, backlog_path=str(backlog), lens_index=None)
    assert not any(f["rule"] == "fabricated-lens-count" for f in res["findings"])
