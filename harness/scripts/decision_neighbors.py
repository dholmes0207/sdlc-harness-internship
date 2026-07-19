#!/usr/bin/env python3
"""decision_neighbors — blast-radius detector for a decision flip.

Split from decision_register by SRP: the register owns CRUD on the SSOT; this
module owns the *analysis* of which other rulings a flip touches. It is pure and
deterministic — the caller passes already-parsed records in, so the register
write path never loads the whole register into a model context (the whole point
of keeping this cheap and inline).

Three relation kinds, weighted strongest-first:
  name-mention  one ruling's rationale names the other by `DEC-<n>` (explicit link)
  affects       the two share an `affects` token, or one's affects names the other
  keyword       title+rationale share >=K rare tokens (Jaccard-style overlap)

classify_scope splits neighbours into in_scope (named in the active plan text)
vs cross_scope (not) — fail-safe: no active plan text → everything is cross_scope,
which leans the gate toward asking rather than silently flipping a far ruling.
"""
import hashlib
import re
from typing import Dict, List, Optional

# Tokens with no discriminating power. Kept deliberately small — the len>=4 floor
# and the digit/dec-id drop already remove most noise; this just kills the common
# connective words (en + a few vi) that would otherwise inflate every overlap.
_STOPWORDS = frozenset((
    "the", "and", "for", "with", "from", "that", "this", "these", "those",
    "into", "onto", "over", "under", "than", "then", "them", "they", "their",
    "have", "has", "had", "will", "would", "shall", "should", "must", "can",
    "cannot", "not", "but", "are", "was", "were", "been", "being", "its",
    "rule", "ruling", "policy", "thing", "stuff",  # register-domain filler
    # vi connectives
    "cua", "cho", "khong", "duoc", "phai", "mot", "cac", "nay", "khi", "neu",
    "thi", "vao", "qua", "theo", "voi", "tren", "duoi",
))

_KEYWORD_K = 2          # min shared rare tokens for a keyword neighbour
_W_NAME = 100           # name-mention dominates
_W_AFFECTS = 10
_TOKEN_RE = re.compile(r"[^0-9a-zA-ZÀ-ỹ]+")  # split on non-alnum (keep vi letters)
_DEC_NUM_RE = re.compile(r"^DEC-(\d+)$")


def _dec_num(dec_id: str) -> int:
    m = _DEC_NUM_RE.match(dec_id or "")
    return int(m.group(1)) if m else 0


def _tokens(text: str) -> frozenset:
    """Discriminating tokens: lowercase, split on non-alnum, drop stopwords,
    drop pure-digit tokens (and the `dec`/number halves of a DEC-id), keep len>=4."""
    out = set()
    for raw in _TOKEN_RE.split(text or ""):
        t = raw.lower()
        if len(t) < 4 or t.isdigit() or t in _STOPWORDS:
            continue
        out.add(t)
    return frozenset(out)


def _mentions(text: str, dec_id: str) -> bool:
    return re.search(r"\b%s\b" % re.escape(dec_id), text or "") is not None


def neighbors(records: List[Dict], target_id: str, *, top_k: int = 8) -> List[Dict]:
    """Neighbours of target_id. Each item: {id, reasons:[...], score}. Deterministic:
    sorted by score desc, tie-broken by DEC-number asc, cut to top_k. The target
    never neighbours itself."""
    by_id = {r.get("id"): r for r in records}
    target = by_id.get(target_id)
    if target is None:
        return []

    t_tokens = _tokens(" ".join((target.get("title", ""), target.get("rationale", ""))))
    t_affects = _tokens(target.get("affects", ""))
    t_text = " ".join((target.get("title", ""), target.get("rationale", ""),
                       target.get("affects", "")))

    out = []
    for r in records:
        nid = r.get("id")
        if nid == target_id or nid is None:
            continue
        reasons, score = [], 0

        n_text = " ".join((r.get("title", ""), r.get("rationale", ""),
                           r.get("affects", "")))
        # name-mention (either direction) — the strongest, explicit link
        if _mentions(n_text, target_id) or _mentions(t_text, nid):
            reasons.append("name-mention")
            score += _W_NAME

        # affects: shared token, or one side names the other's id in affects
        n_affects = _tokens(r.get("affects", ""))
        if (t_affects & n_affects) or _mentions(r.get("affects", ""), target_id) \
                or _mentions(target.get("affects", ""), nid):
            reasons.append("affects")
            score += _W_AFFECTS

        # keyword overlap of title+rationale
        shared = t_tokens & _tokens(" ".join((r.get("title", ""), r.get("rationale", ""))))
        if len(shared) >= _KEYWORD_K:
            reasons.append("keyword")
            score += len(shared)

        if reasons:
            out.append({"id": nid, "reasons": reasons, "score": score})

    out.sort(key=lambda n: (-n["score"], _dec_num(n["id"])))
    return out[:top_k]


def classify_scope(neigh: List[Dict], active_plan_text: Optional[str]) -> Dict[str, List[str]]:
    """Split neighbour ids into in_scope (named in active_plan_text by a
    word-bounded `DEC-<n>`) vs cross_scope. active_plan_text None/empty → ALL
    cross_scope (fail-safe: no plan context means lean toward asking)."""
    in_scope, cross_scope = [], []
    text = active_plan_text or ""
    for n in neigh:
        nid = n["id"]
        if text and _mentions(text, nid):
            in_scope.append(nid)
        else:
            cross_scope.append(nid)
    return {"in_scope": in_scope, "cross_scope": cross_scope}


def implicit_flip_match(records: List[Dict], title: str, rationale: str, *,
                        min_shared: int) -> Optional[str]:
    """The live ruling a proposed NEW (title, rationale) most strongly restates, or
    None. Used to warn on an implicit flip (a new ruling that contradicts an old one
    without `supersedes`). High bar by design — only fires on >= min_shared rare
    shared tokens, so a couple of shared domain words never trips it."""
    new_tokens = _tokens(" ".join((title, rationale)))
    best, best_n = None, 0
    for r in records:
        status = (r.get("status") or "active")
        if status != "active":
            continue
        shared = new_tokens & _tokens(" ".join((r.get("title", ""), r.get("rationale", ""))))
        if len(shared) >= min_shared and len(shared) > best_n:
            best, best_n = r.get("id"), len(shared)
    return best


def neighbors_digest(cross_scope_ids: List[str]) -> str:
    """sha256-12 hex of the sorted id list — binds a confirm token to EXACTLY this
    cross-scope set (order-insensitive; a different set yields a different digest,
    so a stale/generic token cannot cover a different flip)."""
    canon = "\n".join(sorted(cross_scope_ids))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]
