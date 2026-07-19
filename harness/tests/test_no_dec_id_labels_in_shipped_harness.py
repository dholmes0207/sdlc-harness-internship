"""Absence contract: no DEC-<n> labels leak into shipped harness surface.

Convention (memory: no-dev-id-labels-in-shipped-harness): a DEC id belongs ONLY in the
decision ledger (docs/decisions.yaml/.md, the digest) and the register/fence TOOLING that
processes DEC ids. It must NOT be sprinkled into shipped code/skills/rules/data/README as
an inline citation — that couples the product surface to internal audit ids.

This greps the TRACKED harness/ tree (working-tree content) for a literal `DEC-<n>`,
excluding the two legitimate homes:
  - harness/tests/**            (tests may cite a DEC to explain what they pin)
  - the decision/fence tooling   (decision_*.py, audience_fence.py — they parse DEC ids)

Reads from disk (not `git grep`) so a working-tree fix is seen before it is committed.
Gated dev_repo: an installed copy ships the same tree, but the check is meaningful only
where the source lives.
"""

import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DEC_RE = re.compile(r"DEC-\d+")

# The two legitimate homes for a DEC id inside harness/ (everything else is a leak).
_EXCLUDE_PREFIXES = ("harness/tests/",)
_EXCLUDE_NAMES = ("audience_fence.py",)


def _is_tooling(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if rel.startswith("harness/scripts/decision_"):
        return True
    return name in _EXCLUDE_NAMES


def _tracked_harness_files():
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "-c", "core.quotepath=false",
         "ls-files", "-z", "--", "harness/"],
        capture_output=True, text=True, check=True,
    )
    return [p for p in out.stdout.split("\0") if p.strip()]


@pytest.mark.dev_repo
def test_no_dec_id_labels_in_shipped_harness():
    offenders = []
    for rel in _tracked_harness_files():
        if rel.startswith(_EXCLUDE_PREFIXES) or _is_tooling(rel):
            continue
        p = _ROOT / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary / unreadable — no prose DEC label to leak
        for i, line in enumerate(text.splitlines(), 1):
            if _DEC_RE.search(line):
                offenders.append("%s:%d  %s" % (rel, i, line.strip()[:100]))
    assert not offenders, (
        "DEC-<n> label leaked into shipped harness surface (belongs in the ledger/"
        "tooling only):\n" + "\n".join(offenders)
    )
