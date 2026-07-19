#!/usr/bin/env python3
"""gemini_secret_scrub.py — best-effort secret detector for the partner lane.

v1 is WARN-ONLY (accepted-risk): scan() reports what looks like a credential
in a prompt so the chokepoint can shout on stderr — it does NOT mask or block.
The detector is built now so v2 can flip the posture (secret_scrub: block|redact)
without new plumbing. Returns offsets into the raw text, never a masked copy.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretHit:
    pattern: str   # the detector name that matched
    offset: int    # start index into the scanned text


# (name, compiled regex). Ordered by specificity; scan() sorts hits by offset.
_PATTERNS = [
    ("assignment", re.compile(r"\w*_?(?:KEY|TOKEN|SECRET|PASSWORD)\s*=", re.IGNORECASE)),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("bearer_auth", re.compile(r"Authorization:\s*Bearer", re.IGNORECASE)),
]


def scan(text):
    """Return a list of SecretHit (sorted by offset) for every detector match.
    Empty list = nothing suspicious. Never raises on ordinary text."""
    if not text:
        return []
    hits = []
    for name, rx in _PATTERNS:
        for m in rx.finditer(text):
            hits.append(SecretHit(name, m.start()))
    return sorted(hits, key=lambda h: h.offset)
