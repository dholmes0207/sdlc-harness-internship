"""Drift guard for the canonical four-label claim-typing taxonomy.

The harness types every load-bearing claim by the evidence behind it, using four labels:
OBSERVED / DERIVED / PRIOR / ASSUMED. This replaces the older binary single tag. The
canonical definition lives in `verification-mechanism.md`; the evidence ranking (the
5-rung ladder) + "read errors literally" live with probe-first in
`agent-operational-discipline.md` (★); `harness-contract.md` carries the always-load
one-liner. This guard asserts the definition is present and that the three core rules no
longer carry the retired single tag.

The retired tag literal is assembled from fragments so THIS test file does not itself
count as an occurrence of it.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "harness" / "rules"
VERIF = RULES / "verification-mechanism.md"
PROBE = RULES / "agent-operational-discipline.md"
CONTRACT = RULES / "harness-contract.md"

_RETIRED_TAG = "[UN" + "VERIFIED]"          # the retired single tag
_LABELS = ("OBSERVED", "DERIVED", "PRIOR", "ASSUMED")


def test_verification_mechanism_defines_all_four_labels():
    text = VERIF.read_text(encoding="utf-8")
    for label in _LABELS:
        assert re.search(r"\*\*%s\*\*" % label, text), (
            "verification-mechanism.md must define the %s label (bold table row)" % label)
    # the promotion rule that stops laundering an unverified claim into a verified one
    assert "promote" in text.lower() or "only a tool" in text.lower(), (
        "the taxonomy must state that only a tool promotes a claim")


def test_probe_rule_carries_the_evidence_ladder_and_read_errors_literally():
    text = PROBE.read_text(encoding="utf-8").lower()
    for rung in ("direct observation", "reproduction", "primary source",
                 "secondary source", "memory"):
        assert rung in text, "probe-first ★ must carry the evidence rung %r" % rung
    assert "literal" in text, "probe-first ★ must say to read errors literally"


def test_contract_names_the_taxonomy():
    text = CONTRACT.read_text(encoding="utf-8")
    assert "OBSERVED" in text and "ASSUMED" in text, (
        "harness-contract one-liner must name the claim-typing labels")


def test_core_rules_dropped_the_retired_tag():
    for rule in (VERIF, PROBE, CONTRACT):
        text = rule.read_text(encoding="utf-8")
        assert _RETIRED_TAG not in text, (
            "%s still carries the retired single tag — use the 4-label taxonomy" % rule.name)


# --- completion gate: the tier-1 sweep left NO bracketed claim-typing tag ---------
# Two documented carve-outs stay by design (see plans/.../USER-DECISION.md UD-13/UD-14):
#   - cti-expert keeps `UNVERIFIED` as one rung of its OSINT source-confidence SCALE
#     (a domain tradecraft vocabulary, not the harness claim-typing tag);
#   - the tier-2 orchestrator keeps its own code-enforced `[UNVERIFIED]` verify-state
#     (the `IMPLEMENTED_UNVERIFIED` constant), so docs/product, docs/orchestrator,
#     docs/standards and orchestrator/ are NOT in the tier-1 scope below.
_TIER1_ROOTS = (
    REPO_ROOT / "harness",
    REPO_ROOT / "docs" / "harness",
    REPO_ROOT / "docs" / "showcase",
    # the git-tracked showcase BUILD output: if a partial is swept but `public/` is not
    # rebuilt, the retired tag survives here -- scanning it closes that CI blind spot.
    REPO_ROOT / "public",
)
_TIER1_FILES = (
    REPO_ROOT / "docs" / "STANDARDIZE.md",
    REPO_ROOT / "docs" / "decisions.md",
    REPO_ROOT / "docs" / "decisions.yaml",
    REPO_ROOT / "docs" / "backlog.yaml",
    REPO_ROOT / "BACKLOG.md",
)
_CTI_EXPERT = REPO_ROOT / "harness" / "plugins" / "hs" / "skills" / "cti-expert"


def _tier1_text_files():
    seen = []
    for root in _TIER1_ROOTS:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or _CTI_EXPERT in p.parents or p == Path(__file__):
                continue
            if p.suffix in (".md", ".yaml", ".yml", ".py", ".html", ".txt"):
                seen.append(p)
    seen += [p for p in _TIER1_FILES if p.is_file()]
    return seen


def test_tier1_scope_carries_no_bracketed_retired_tag():
    """Completion gate for the sweep: the tier-1 harness scope carries 0 bracketed
    claim-typing tags (the retired single tag). The two carve-outs above are excluded
    by construction, not by whitelisting individual hits."""
    offenders = []
    for p in _tier1_text_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _RETIRED_TAG in text:
            offenders.append(str(p.relative_to(REPO_ROOT)))
    assert not offenders, (
        "tier-1 files still carry the retired claim-typing tag — migrate to the "
        "4-label taxonomy:\n" + "\n".join(sorted(offenders)))
