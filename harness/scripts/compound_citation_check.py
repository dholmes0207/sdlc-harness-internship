#!/usr/bin/env python3
"""compound_citation_check.py — re-verify the citations in an hs:compound proposal.

hs:compound proposes harness self-improvements, each grounded in a citation of one of
two kinds:

  - a BACKLOG item       BACKLOG.md:NNN   or   BACKLOG:NNN
  - a telemetry lens count    lens:<name> <sub_key> "<chain>" count=N

A model can fabricate either — cite a BACKLOG line that does not exist, or inflate a
chain count to make a proposal look better-grounded than it is. This checker
re-derives each citation from the primary source (the BACKLOG file; a lens index) and
FLAGS fabrications. It deliberately does NOT remove a flagged proposal: mirroring the
source's rejected-stays-visible rule, a proposal whose citation fails is still shown,
just marked unverified — the human keeps the call.

Two legs, independent:
  - BACKLOG-ID leg: needs only the BACKLOG file. Always runs.
  - lens-count leg: needs a lens index (chain -> actual count). Skipped (not failed)
    when no index is supplied — a missing index is missing evidence, not a fabrication.

Advisory by contract: exits 0, prints a JSON verdict. --strict exits non-zero when any
citation is fabricated, for a caller that wants to gate.

CLI:
    compound_citation_check.py --proposal FILE [--backlog BACKLOG.md]
                               [--lens-json FILE] [--strict]
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# BACKLOG.md:NNN or BACKLOG:NNN — the line number is what gets verified to exist.
_BACKLOG_RE = re.compile(r"\bBACKLOG(?:\.md)?:(\d+)\b")
# lens:<name> <sub_key> "<chain>" count=N — the chain string keys the lens index.
_LENS_RE = re.compile(r'lens:(\S+)\s+(\S+)\s+"([^"]*)"\s+count=(\d+)')

# Lens counts within this absolute tolerance of the cited value are not fabrications —
# a lens recomputed at a slightly different time can drift by one.
_COUNT_TOLERANCE = 1


def _backlog_line_count(backlog_path) -> Optional[int]:
    """Number of lines in the BACKLOG file, or None when it cannot be read."""
    try:
        with open(backlog_path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def check_backlog_ids(text: str, backlog_path) -> List[dict]:
    """Flag every cited BACKLOG line number that does not exist in the file."""
    total = _backlog_line_count(backlog_path)
    findings: List[dict] = []
    if total is None:
        # No BACKLOG to verify against — surface that rather than passing silently.
        if _BACKLOG_RE.search(text):
            findings.append({"rule": "backlog-unreadable", "severity": "advisory",
                             "detail": "cannot read %s to verify BACKLOG citations" % backlog_path})
        return findings
    for m in _BACKLOG_RE.finditer(text):
        n = int(m.group(1))
        if not (1 <= n <= total):
            findings.append({"rule": "fabricated-backlog-id", "severity": "advisory",
                             "detail": "BACKLOG line %d does not exist (file has %d lines)"
                                       % (n, total)})
    return findings


def check_lens_counts(text: str, lens_index: Optional[Dict[str, int]]) -> List[dict]:
    """Flag every lens count that disagrees with the index beyond ±tolerance.

    A None index skips the leg entirely (missing evidence, not a fabrication).
    """
    if lens_index is None:
        return []
    findings: List[dict] = []
    for m in _LENS_RE.finditer(text):
        name, sub_key, chain, cited = m.group(1), m.group(2), m.group(3), int(m.group(4))
        if chain not in lens_index:
            findings.append({"rule": "lens-chain-unknown", "severity": "advisory",
                             "detail": 'lens:%s %s "%s" — chain not found in lens index'
                                       % (name, sub_key, chain)})
            continue
        actual = lens_index[chain]
        if abs(cited - actual) > _COUNT_TOLERANCE:
            findings.append({"rule": "fabricated-lens-count", "severity": "advisory",
                             "detail": 'lens:%s %s "%s" cited count=%d but actual=%d'
                                       % (name, sub_key, chain, cited, actual)})
    return findings


def check_citations(text: str, backlog_path, lens_index: Optional[Dict[str, int]]) -> Dict[str, Any]:
    """Re-verify both citation legs. The proposal is ALWAYS shown — a fabricated
    citation flags the proposal (verdict PASS_WITH_RISK) but never hides it."""
    findings = check_backlog_ids(text, backlog_path) + check_lens_counts(text, lens_index)
    return {
        "tool": "compound_citation_check",
        "verdict": "PASS_WITH_RISK" if findings else "PASS",
        "findings": findings,
        "proposal_shown": True,
    }


def _load_lens_index(path) -> Optional[Dict[str, int]]:
    """Load a chain -> count map from a JSON file. None when no path is given or unreadable."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Re-verify citations in an hs:compound proposal.")
    ap.add_argument("--proposal", required=True, help="file holding the proposal text")
    ap.add_argument("--backlog", default="BACKLOG.md", help="BACKLOG file to verify against")
    ap.add_argument("--lens-json", default=None,
                    help="JSON dict {chain: count} to verify lens citations (skip leg if absent)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero when any citation is fabricated")
    args = ap.parse_args(argv)

    try:
        text = Path(args.proposal).read_text(encoding="utf-8")
    except OSError as exc:
        print(json.dumps(
            {"tool": "compound_citation_check", "error": str(exc),
             "proposal_shown": False},
            ensure_ascii=False, indent=2))
        return 0
    lens_index = _load_lens_index(args.lens_json)
    result = check_citations(text, backlog_path=args.backlog, lens_index=lens_index)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    fabricated = any(f["rule"].startswith("fabricated-") for f in result["findings"])
    return 1 if (args.strict and fabricated) else 0


if __name__ == "__main__":
    sys.exit(main())
