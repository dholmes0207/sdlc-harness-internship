"""Shared brand/OCR denylist for the eval-bootstrap template guards.

One home for the two guard regexes so a newly discovered leaked brand or a
forbidden OCR import is added ONCE. A second copy in another test file would
silently drift — someone updates one file, the other keeps scanning the stale
list and stays green on a real leak.
"""

import re

BRAND_RE = re.compile(
    r"frankode|frankcode|ehiring-ai|kb guard|plan-2333|auditcore", re.IGNORECASE)
OCR_RE = re.compile(r"pytesseract|from\s+PIL\b|import\s+PIL\b")
