"""Walk docs/ → tập Doc (md có/không frontmatter). Bỏ qua zone raw/archive/generated + .docsignore."""
from __future__ import annotations

from pathlib import Path

from .frontmatter import parse, Doc

SKIP_DIRS = {"_inbox", "_archive", "_generated", "__pycache__", ".git", "node_modules"}


def _load_docsignore(docs_root: Path) -> list[str]:
    """Đọc .docsignore ở docs_root, trả về list pattern (loại comment/dòng trống)."""
    ignore_file = docs_root / ".docsignore"
    if not ignore_file.is_file():
        return []
    patterns: list[str] = []
    for line in ignore_file.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped)
    return patterns


def _is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Kiểm tra rel_path (so với docs_root) có khớp bất kỳ pattern nào không.

    Pattern đơn giản = prefix match theo segment — thư mục/ hoặc file. Đủ cho
    exclude cả cây product/, không cần globstar/fnmatch full.
    """
    for pat in patterns:
        # Directory pattern: "product/" hoặc "product/*" → match prefix
        if pat.endswith("/") and rel_path.startswith(pat):
            return True
        # File/dir exact pattern
        if pat.endswith("/*") and rel_path.startswith(pat[:-1]):
            return True
        if rel_path == pat or rel_path.startswith(pat + "/"):
            return True
    return False


def iter_md(docs_root: str | Path, ignore_patterns: list[str] | None = None):
    """Yield path mọi .md trong ZONE-2 (bỏ raw/archive/generated + .docsignore)."""
    docs_root = Path(docs_root)
    if ignore_patterns is None:
        ignore_patterns = _load_docsignore(docs_root)
    for p in sorted(docs_root.rglob("*.md")):
        rel_parts = p.relative_to(docs_root).parts
        if any(seg in SKIP_DIRS for seg in rel_parts):
            continue
        # Code-default skip (top-level only, so a nested .../product/ is NOT
        # over-matched): docs/product/ is generated + validated in-harness by
        # hs:spec (its own strict_gate/validate), not by harness docs governance.
        if rel_parts and rel_parts[0] == "product":
            continue
        rel = str(p.relative_to(docs_root))
        if _is_ignored(rel, ignore_patterns):
            continue
        yield p


def discover(docs_root: str | Path, ignore_patterns: list[str] | None = None) -> list[Doc]:
    docs_root = Path(docs_root)
    if ignore_patterns is None:
        ignore_patterns = _load_docsignore(docs_root)
    out = []
    for p in iter_md(docs_root, ignore_patterns):
        out.append(parse(p, rel=str(p.relative_to(docs_root))))
    return out
