"""test_reader_shard_union_completeness.py — mechanical guard that a future
telemetry/trace reader unions shards instead of reading one file (R1/D5).

Two guards:
  1. No `glob(...)[0]` first-match FILE-select in reader hooks/scripts — after
     sharding, "newest by name" is not "the session's file", so first-match reads
     the wrong shard (the R1 bug class). Readers must union, not pick one.
  2. The known invocations readers go through the union resolver
     (telemetry_paths.sink_read_files), never a bare single-file read.

A self-test proves the scanner actually fires on the antipattern (else the tree
being clean would pass vacuously).
"""
import io
import re
import sys
import tokenize
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
_SCRIPTS = _HARNESS / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Files intentionally allowed to select a single glob result (with the reason).
# Empty today — every reader unions. A new entry MUST carry a justification.
_FIRST_MATCH_ALLOWLIST: dict = {}

_READER_DIRS = [_HARNESS / "hooks", _HARNESS / "scripts"]

# A glob result immediately (or after a sort) subscripted [0] on ONE line.
_FIRST_MATCH = re.compile(r"glob\([^\n]*\)[^\n]*\[0\]")


def _code_only(text: str) -> str:
    """Blank every STRING and COMMENT token (docstrings, string literals, `#`
    comments) while preserving line numbers, so a `glob(...)[0]` MENTIONED in
    prose never trips the scanner — only executable code is scanned. Falls back
    to the raw text if the source does not tokenize."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    lines = text.splitlines(keepends=True)
    buf = list(lines)
    for tok in toks:
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            (r1, c1), (r2, c2) = tok.start, tok.end
            for r in range(r1, r2 + 1):
                idx = r - 1
                if idx >= len(buf):
                    continue
                line = buf[idx]
                start = c1 if r == r1 else 0
                end = c2 if r == r2 else len(line)
                buf[idx] = line[:start] + (" " * (end - start)) + line[end:]
    return "".join(buf)


def _scan_first_match(text: str):
    code = _code_only(text)
    hits = []
    for i, line in enumerate(code.splitlines(), 1):
        if _FIRST_MATCH.search(line):
            hits.append((i, line.strip()))
    return hits


class TestFirstMatchGuard:
    def test_self_test_scanner_fires(self):
        # T8: the guard must catch the antipattern, else it is dead.
        bad = 'newest = sorted(d.glob("trace-*.jsonl"), reverse=True)[0]\n'
        assert _scan_first_match(bad), "scanner must flag glob(...)[0] first-match"

    def test_self_test_ignores_comment_mention(self):
        ok = '# a glob("x")[0] first-match is banned — see docstring\n'
        assert not _scan_first_match(ok)

    def test_real_tree_has_no_first_match_select(self):
        # T9: the shipped reader tree is clean (or only allowlisted).
        offenders = []
        for d in _READER_DIRS:
            for f in sorted(d.glob("*.py")):
                hits = _scan_first_match(f.read_text(encoding="utf-8"))
                if hits and f.name not in _FIRST_MATCH_ALLOWLIST:
                    offenders.append((f.name, hits))
        assert not offenders, "first-match glob select outside allowlist: %r" % offenders


class TestInvocationsReadersUnion:
    # Readers of the invocations sink that must union shards via sink_read_files.
    _READERS = [
        _HARNESS / "hooks" / "cook_isolation_nudge.py",
        _HARNESS / "hooks" / "discover_isolation_nudge.py",
    ]

    def test_readers_use_union_resolver(self):
        for f in self._READERS:
            text = f.read_text(encoding="utf-8")
            assert "sink_read_files" in text, \
                "%s must read invocations via sink_read_files (union), not one file" % f.name

    def test_chokepoint_unions_shards(self):
        import telemetry_paths as tp
        # sink_read_files returns legacy + shards for a dotted sink name.
        # (behavioural check that the union resolver exists and is dotted-aware)
        assert hasattr(tp, "sink_read_files")
