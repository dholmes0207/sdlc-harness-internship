"""hs:spec — per-workspace DEC ledger allocator (dec_ledger.py).

Ledger = `<root>/docs/product/decisions.md` (markdown, YAML-frontmatter blocks).
Allocation is grep max `## DEC-<n>` heading +1 (gap-preserving, not gap-filling)
under an exclusive `fcntl.flock` on a sibling `.decisions.lock` file — never the
harness `decision_register.py --append-alloc` mechanism (that allocator double-
allocates on a hand-maintained markdown ledger; memory
`product-spec-decision-register-ledger-mismatch`).
"""

from __future__ import annotations

import concurrent.futures
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Literal path below keeps the stashed-skill collect_ignore coupling working:
# harness/plugins/hs/skills/spec/scripts
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_mods = load_skill_scripts(_SPEC_SCRIPTS, ["encoding_utils", "id_grammar", "dec_ledger"])
id_grammar = _mods["id_grammar"]
dec_ledger = _mods["dec_ledger"]

_HEADING_RE = re.compile(r"^##\s+(DEC-[0-9]+)\b", re.MULTILINE)


def _ledger_path(root) -> Path:
    return Path(root) / "docs" / "product" / "decisions.md"


def _lock_path(root) -> Path:
    return Path(root) / "docs" / "product" / ".decisions.lock"


def _headings(root) -> list:
    text = _ledger_path(root).read_text(encoding="utf-8")
    return _HEADING_RE.findall(text)


# ---------------------------------------------------------------------------
# First alloc / consecutive allocs
# ---------------------------------------------------------------------------

def test_first_alloc_on_empty_ledger(tmp_path):
    dec_id = dec_ledger.alloc(tmp_path, title="First decision", body="Body text.")
    assert dec_id == "DEC-1"
    assert id_grammar.DEC_ID_PATTERN.match(dec_id)
    assert _headings(tmp_path) == ["DEC-1"]


def test_two_consecutive_allocs_no_dup(tmp_path):
    first = dec_ledger.alloc(tmp_path, title="A", body="a")
    second = dec_ledger.alloc(tmp_path, title="B", body="b")
    third = dec_ledger.alloc(tmp_path, title="C", body="c")
    assert (first, second, third) == ("DEC-1", "DEC-2", "DEC-3")
    assert _headings(tmp_path) == ["DEC-1", "DEC-2", "DEC-3"]


# ---------------------------------------------------------------------------
# Gap ledger: allocate max+1, never fill a hole
# ---------------------------------------------------------------------------

def test_gap_ledger_allocates_max_plus_one_not_a_gap(tmp_path):
    seeded = (
        "# Decision Register\n"
        "\n---\nid: DEC-1\nstatus: active\ndate: 2026-07-01\nactor: po\n"
        "ts: 2026-07-01T00:00:00+00:00\naffects: X\n---\n\n"
        "## DEC-1 — Seed one\n\nBody one.\n"
        "\n---\nid: DEC-5\nstatus: active\ndate: 2026-07-01\nactor: po\n"
        "ts: 2026-07-01T00:00:00+00:00\naffects: Y\n---\n\n"
        "## DEC-5 — Seed five (2,3,4 missing)\n\nBody five.\n"
    )
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(seeded, encoding="utf-8")

    new_id = dec_ledger.alloc(tmp_path, title="Six", body="six")
    assert new_id == "DEC-6"
    assert "DEC-2" not in _headings(tmp_path)  # gap NOT filled
    assert _headings(tmp_path) == ["DEC-1", "DEC-5", "DEC-6"]


# ---------------------------------------------------------------------------
# A --body carrying a fake `## DEC-<n>` heading must not be scanned as a real id
# (the tool defuses body headings before writing, so the counter is not moved).
# ---------------------------------------------------------------------------

def test_body_with_fake_heading_never_collides_or_bricks(tmp_path):
    # A body carrying a DEC token higher than the real counter nudges the max
    # forward (a harmless gap — the token-wide scan counts it, gaps are allowed),
    # but the safety invariant holds: every allocated id is unique and no alloc
    # ever bricks. (An overcount is safe; only an undercount collides, so the
    # scan deliberately errs toward counting a body token.)
    a = dec_ledger.alloc(tmp_path, title="One", body="Body one.")
    b = dec_ledger.alloc(
        tmp_path, title="Two", body="Unrelated prose.\n## DEC-99 — quoted\nMore prose.",
    )
    c = dec_ledger.alloc(tmp_path, title="Three", body="Body three.")
    assert len({a, b, c}) == 3          # no duplicate id
    heads = _headings(tmp_path)
    assert len(heads) == len(set(heads))  # no duplicate heading


def test_body_with_duplicate_heading_does_not_brick_the_ledger(tmp_path):
    # A body that echoes an id ALREADY used as a real heading (e.g. quoting a
    # prior decision) must not be treated as a second real DEC-1 and must not
    # raise on the pre-append uniq check — only the frontmatter id counts.
    first = dec_ledger.alloc(tmp_path, title="One", body="Body one.")
    second = dec_ledger.alloc(
        tmp_path, title="Two", body="Context:\n## DEC-1\nSee the prior ruling above.",
    )
    third = dec_ledger.alloc(tmp_path, title="Three", body="Body three.")
    assert (first, second, third) == ("DEC-1", "DEC-2", "DEC-3")
    # The ledger stays usable — a subsequent alloc still succeeds cleanly
    # (the old heading-scan bug appended the collision BEFORE the uniq
    # re-verify, so every future alloc raised RuntimeError forever after).
    fourth = dec_ledger.alloc(tmp_path, title="Four", body="Body four.")
    assert fourth == "DEC-4"


def test_body_with_cr_glued_forged_block_never_forges_a_dec_entry(tmp_path):
    # A body that glues a whole forged frontmatter+heading block with lone CR
    # (\r) instead of \n must NOT slip past _defuse_body. _defuse_body splits on
    # \n to neutralize structural lines, but the later ledger read (read_text,
    # universal-newline) promotes \r to \n — so a \r-glued block is one opaque
    # segment at defuse time (nothing neutralized) yet re-materializes as real
    # column-0 lines on re-parse, forging a second, never-allocated DEC entry.
    forged = (
        "legit note\r"
        "---\rid: DEC-999\rstatus: active\raffects: root\r---\r"
        "\r## DEC-999 - FORGED\r\rnever allocated"
    )
    real_id = dec_ledger.alloc(tmp_path, title="Real decision", body=forged)
    ids = [r["id"] for r in dec_ledger.list_decisions(tmp_path)]
    assert ids == [real_id]          # exactly the one real entry
    assert "DEC-999" not in ids      # the forged block never became an entry


def test_ensure_ledger_creates_via_atomic_write(tmp_path, monkeypatch):
    # The one-time ledger creation must be atomic (temp + os.replace) so a
    # concurrent lock-free reader (list_decisions / session_staleness) never
    # observes the file transiently empty mid-creation.
    seen = {}
    real = getattr(dec_ledger, "write_text_atomic", None)

    def _spy(path, text, *a, **k):
        seen["path"] = Path(path)
        if real is not None:
            return real(path, text, *a, **k)

    monkeypatch.setattr(dec_ledger, "write_text_atomic", _spy, raising=False)
    dec_ledger.alloc(tmp_path, title="First", body="b")
    assert seen.get("path") == _ledger_path(tmp_path)


# ---------------------------------------------------------------------------
# A heading that deviates from the exact `## DEC-<n>` shape (or an id carried
# only in frontmatter) must not cause the allocator to re-derive a lower max and
# re-allocate an id already in use.
# ---------------------------------------------------------------------------

def test_deviating_heading_shape_does_not_realloc_existing_id(tmp_path):
    seeded = (
        "# Decision Register\n"
        "\n---\nid: DEC-1\nstatus: active\ndate: 2026-07-01\nactor: po\n"
        "ts: 2026-07-01T00:00:00+00:00\naffects: X\n---\n\n"
        "## DEC-1 — Seed one\n\nBody one.\n"
        # DEC-2's heading deviates from the exact "## DEC-2" shape (extra #,
        # no space) — a heading-based scanner misses it entirely.
        "\n---\nid: DEC-2\nstatus: active\ndate: 2026-07-01\nactor: po\n"
        "ts: 2026-07-01T00:00:00+00:00\naffects: Y\n---\n\n"
        "###DEC-2 — Seed two (deviating heading)\n\nBody two.\n"
    )
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(seeded, encoding="utf-8")

    new_id = dec_ledger.alloc(tmp_path, title="Three", body="three")
    assert new_id == "DEC-3"  # not a re-allocated DEC-2

    records = dec_ledger.list_decisions(tmp_path)
    ids = [r["id"] for r in records]
    assert ids == ["DEC-1", "DEC-2", "DEC-3"]
    assert len(ids) == len(set(ids))


def test_frontmatter_only_id_with_no_heading_counted(tmp_path):
    # DEC-2 carries its id ONLY in frontmatter (no `## DEC-2` heading line at
    # all) — the allocator must still see it via the frontmatter field.
    seeded = (
        "# Decision Register\n"
        "\n---\nid: DEC-1\nstatus: active\ndate: 2026-07-01\nactor: po\n"
        "ts: 2026-07-01T00:00:00+00:00\naffects: X\n---\n\n"
        "## DEC-1 — Seed one\n\nBody one.\n"
        "\n---\nid: DEC-2\nstatus: active\ndate: 2026-07-01\nactor: po\n"
        "ts: 2026-07-01T00:00:00+00:00\naffects: Y\n---\n\n"
        "Body two, no heading at all.\n"
    )
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(seeded, encoding="utf-8")

    new_id = dec_ledger.alloc(tmp_path, title="Three", body="three")
    assert new_id == "DEC-3"


# ---------------------------------------------------------------------------
# uniq -d verify
# ---------------------------------------------------------------------------

def test_uniq_verify_empty_after_several_allocs(tmp_path):
    for _ in range(4):
        dec_ledger.alloc(tmp_path, title="t", body="b")
    ids = _headings(tmp_path)
    assert len(ids) == len(set(ids))


def test_alloc_on_a_pre_existing_duplicate_ledger_does_not_brick(tmp_path):
    # A hand-maintained ledger already carrying a duplicate heading must NOT
    # block a new allocation: max+1 is collision-free by construction, so the
    # allocator allocates past the corruption instead of bricking on it.
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n"
        "\n## DEC-1 — one\n\nBody one.\n"
        "\n## DEC-2 — two\n\nBody two.\n"
        "\n## DEC-1 — a hand-edited duplicate\n\nBody dup.\n",
        encoding="utf-8",
    )
    assert dec_ledger.alloc(tmp_path, title="t", body="b") == "DEC-3"


# ---------------------------------------------------------------------------
# Race-safety — flock sibling exists + guards the critical section under real
# races
# ---------------------------------------------------------------------------

def test_lock_sibling_created_after_add(tmp_path):
    dec_ledger.alloc(tmp_path, title="t", body="b")
    assert _lock_path(tmp_path).exists()


def test_concurrent_allocs_never_collide(tmp_path):
    # Real race: N threads hit alloc() on the SAME workspace simultaneously.
    # Each open()s its own fd onto .decisions.lock, so flock only serializes
    # them if the critical section genuinely holds the lock end-to-end.
    n = 12

    def _do(i):
        return dec_ledger.alloc(tmp_path, title="race-%d" % i, body="b")

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(_do, range(n)))

    assert len(results) == len(set(results)) == n
    assert sorted(results, key=lambda d: int(d.split("-", 1)[1])) == [
        "DEC-%d" % i for i in range(1, n + 1)
    ]
    assert _headings(tmp_path) == ["DEC-%d" % i for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# Append-only: prior bytes never rewritten
# ---------------------------------------------------------------------------

def test_append_only_prior_block_byte_unchanged(tmp_path):
    dec_ledger.alloc(tmp_path, title="First", body="Body one.")
    before = _ledger_path(tmp_path).read_bytes()

    dec_ledger.alloc(tmp_path, title="Second", body="Body two.")
    after = _ledger_path(tmp_path).read_bytes()

    assert after.startswith(before)
    assert len(after) > len(before)


# ---------------------------------------------------------------------------
# Schema-valid block
# ---------------------------------------------------------------------------

def test_new_block_is_schema_valid(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    import json

    dec_ledger.alloc(
        tmp_path, status="active", affects=["PRD-X", "PRD-X-E1"],
        title="Schema check", body="Body.",
    )
    records = dec_ledger.list_decisions(tmp_path)
    assert records, "expected at least one parsed DEC block"
    last = records[-1]

    schema_path = (
        ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "schemas"
        / "decision.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=last, schema=schema)


# ---------------------------------------------------------------------------
# Guard: never the harness --append-alloc mechanism
# ---------------------------------------------------------------------------

def test_source_never_references_append_alloc_or_decision_register():
    src = (_SPEC_SCRIPTS / "dec_ledger.py").read_text(encoding="utf-8")
    assert "append-alloc" not in src.lower()
    assert "append_alloc" not in src.lower()
    assert "decision_register" not in src.lower()


# ---------------------------------------------------------------------------
# Per-workspace isolation
# ---------------------------------------------------------------------------

def test_per_workspace_roots_are_independent(tmp_path):
    root_a = tmp_path / "workspace-a"
    root_b = tmp_path / "workspace-b"
    root_a.mkdir()
    root_b.mkdir()

    a1 = dec_ledger.alloc(root_a, title="a1", body="a1")
    b1 = dec_ledger.alloc(root_b, title="b1", body="b1")
    a2 = dec_ledger.alloc(root_a, title="a2", body="a2")

    assert (a1, b1, a2) == ("DEC-1", "DEC-1", "DEC-2")
    assert _ledger_path(root_a) != _ledger_path(root_b)
    assert _headings(root_a) == ["DEC-1", "DEC-2"]
    assert _headings(root_b) == ["DEC-1"]


# ---------------------------------------------------------------------------
# CLI smoke: --add / --list
# ---------------------------------------------------------------------------

def test_cli_add_then_list(tmp_path):
    script = _SPEC_SCRIPTS / "dec_ledger.py"
    add = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--add",
         "--status", "active", "--affects", "PRD-X,PRD-X-E1",
         "--title", "CLI decision", "--body", "Made via CLI."],
        capture_output=True, text=True, check=True,
    )
    assert "DEC-1" in add.stdout

    listed = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--list"],
        capture_output=True, text=True, check=True,
    )
    assert "DEC-1" in listed.stdout


# ---------------------------------------------------------------------------
# Heading-based allocation, body-injection defuse, and bad-date tolerance:
# allocate off the heading (the universal id location), defuse body-injected
# markers, tolerate a malformed-date block on read.
# ---------------------------------------------------------------------------

def test_alloc_counts_heading_only_legacy_entries(tmp_path):
    # A hand-authored ledger whose entries carry a `## DEC-<n>` heading but NO
    # frontmatter id block (the legacy shape the real in-repo ledger uses). The
    # allocator must count the headings, not undercount to the frontmatter ids.
    legacy = (
        "# Decision Register\n"
        "\n## DEC-1 — legacy one\n\nRationale one.\n"
        "\n## DEC-2 — legacy two\n\nRationale two.\n"
        "\n## DEC-3 — legacy three\n\nRationale three.\n"
    )
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(legacy, encoding="utf-8")
    assert dec_ledger.alloc(tmp_path, title="Four", body="four") == "DEC-4"


def test_body_injection_never_collides_or_bricks(tmp_path):
    # A body that quotes an example heading / frontmatter fence may nudge the max
    # forward (a harmless gap), but must never brick the ledger or re-allocate a
    # live id. The safety invariant is uniqueness, not contiguity.
    a = dec_ledger.alloc(tmp_path, title="One", body="see\n## DEC-9999\n---\nid: DEC-500\n---\nend")
    b = dec_ledger.alloc(tmp_path, title="Two", body="plain")  # still allocates, no brick
    assert a != b
    heads = _headings(tmp_path)
    assert len(heads) == len(set(heads))


def test_bad_date_frontmatter_does_not_crash(tmp_path):
    # A hand-edited block with an out-of-range bare date (PyYAML raises a bare
    # ValueError, not a YAMLError) must not crash list_decisions or alloc.
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n"
        "\n---\nid: DEC-1\nstatus: active\ndate: 2026-13-99\n---\n"
        "\n## DEC-1 — bad date\n\nBody.\n",
        encoding="utf-8",
    )
    assert isinstance(dec_ledger.list_decisions(tmp_path), list)  # no crash
    assert dec_ledger.alloc(tmp_path, title="Two", body="ok") == "DEC-2"


# ---------------------------------------------------------------------------
# Heading token-scan (combined/decorated), title injection, fail-soft on any
# malformed frontmatter, and no brick on a pre-existing hand-edit duplicate.
# ---------------------------------------------------------------------------

def test_combined_heading_counts_every_token(tmp_path):
    # `## DEC-2 & DEC-3` carries two ids on one line — both must count so the
    # max is DEC-3 and the next alloc is DEC-4, never a duplicate DEC-3.
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n\n## DEC-1 — a\n\nx\n\n## DEC-2 & DEC-3 — combo\n\ny\n",
        encoding="utf-8",
    )
    assert dec_ledger.alloc(tmp_path, title="t", body="b") == "DEC-4"


def test_decorated_heading_is_counted(tmp_path):
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n\n## DEC-1 — a\n\nx\n\n## ~~DEC-3~~ superseded\n\ny\n",
        encoding="utf-8",
    )
    assert dec_ledger.alloc(tmp_path, title="t", body="b") == "DEC-4"


def test_title_with_newline_cannot_forge_a_duplicate(tmp_path):
    # A title carrying a newline + a heading-shaped line must not forge a SECOND
    # heading line the scan mis-reads. The newline is collapsed, so at worst a
    # same-line DEC token raises the max (a harmless gap) — never a COLLISION or
    # a brick. Assert the invariant that matters: every allocated id is unique
    # and the ledger never bricks.
    a = dec_ledger.alloc(tmp_path, title="One", body="b")
    b = dec_ledger.alloc(tmp_path, title="legit\n## DEC-500", body="b")
    c = dec_ledger.alloc(tmp_path, title="Three", body="b")  # still allocates, no brick
    assert len({a, b, c}) == 3          # no duplicate id
    heads = _headings(tmp_path)
    assert len(heads) == len(set(heads))  # no duplicate heading in the ledger


def test_hand_edited_duplicate_heading_does_not_brick(tmp_path):
    # A pre-existing hand-edit with a duplicate heading must NOT block a new
    # alloc (max+1 is collision-free; the old whole-ledger uniq gate bricked here).
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n\n## DEC-1 — a\n\n## DEC-1 was the precedent.\n",
        encoding="utf-8",
    )
    assert dec_ledger.alloc(tmp_path, title="t", body="b") == "DEC-2"


def test_explicit_timestamp_tag_frontmatter_does_not_crash(tmp_path):
    # `!!timestamp 'not a ts'` makes PyYAML raise a bare AttributeError (neither
    # YAMLError nor ValueError) — the reader must fail soft, not crash.
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n\n---\nid: DEC-1\nx: !!timestamp 'not a ts'\n---\n\n## DEC-1 — a\n\nx\n",
        encoding="utf-8",
    )
    assert isinstance(dec_ledger.list_decisions(tmp_path), list)
    assert dec_ledger.alloc(tmp_path, title="t", body="b") == "DEC-2"


# ---------------------------------------------------------------------------
# An emphasis-wrapped heading (`## _DEC-1_ — Title`) must still be recognized
# as the frontmatter block's heading, matching _existing_ids' lookaround
# boundary (`_` is a word char under `\b`, so a plain `\b`-based heading regex
# misses it and the block is silently dropped from list_decisions()).
# ---------------------------------------------------------------------------

def test_emphasis_wrapped_heading_is_recognized_and_listed(tmp_path):
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n"
        "\n---\nid: DEC-1\nstatus: active\ndate: 2026-07-01\nactor: po\n"
        "ts: 2026-07-01T00:00:00+00:00\naffects: X\n---\n\n"
        "## _DEC-1_ — Title\n\nBody.\n",
        encoding="utf-8",
    )
    ids = [r["id"] for r in dec_ledger.list_decisions(tmp_path)]
    assert "DEC-1" in ids


def test_defuse_body_neutralizes_indented_heading(tmp_path):
    # The heading regex tolerates 0-3 space indent (CommonMark). A body quoting
    # an example `## DEC-<k>` heading at ANY of those indents must be defused so
    # a single-space shift can no longer leave it matching the heading regex.
    for indent in ("", " ", "  ", "   "):
        defused = dec_ledger._defuse_body(indent + "## DEC-999 — example heading")
        assert dec_ledger._HEADING_WITH_DEC_RE.match(defused) is None, (
            "indent=%r still matched heading regex after defuse" % indent
        )


def test_defuse_body_neutralizes_no_space_dec_heading(tmp_path):
    # _HEADING_WITH_DEC_RE deliberately matches a no-space hand-typo heading
    # (`##DEC-<k>`); _defuse_body must neutralize that variant too, not only
    # the space-delimited form, so the defuse fully covers the heading regex.
    for line in ("##DEC-999forged", " ##DEC-42", "###DEC-1x"):
        defused = dec_ledger._defuse_body(line)
        assert dec_ledger._HEADING_WITH_DEC_RE.match(defused) is None, repr(line)


# ---------------------------------------------------------------------------
# CommonMark allows an ATX heading indented up to 3 spaces. A real, well-formed
# DEC block whose heading is indented (a markdown-linter/copy-paste artifact)
# must still be recognized as the block's heading -- a col-0-anchored heading
# regex silently drops it from list_decisions() even though _existing_ids still
# reserves the number (so no collision, but a real decision goes invisible).
# ---------------------------------------------------------------------------

def test_indented_heading_is_recognized_and_listed(tmp_path):
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "# Decision Register\n"
        "\n---\nid: DEC-1\nstatus: active\ndate: 2026-07-01\nactor: po\n"
        "ts: 2026-07-01T00:00:00+00:00\naffects: X\n---\n\n"
        "  ## DEC-1 — Title\n\nBody.\n",
        encoding="utf-8",
    )
    ids = [r["id"] for r in dec_ledger.list_decisions(tmp_path)]
    assert "DEC-1" in ids


def _bounded(fn, seconds=4):
    """Run fn() under SIGALRM so a blocking-read regression FAILS instead of
    hanging the suite."""
    import signal

    class _Blocked(Exception):
        pass

    def _handler(signum, frame):
        raise _Blocked

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_list_decisions_skips_fifo_ledger(tmp_path):
    # list_decisions is reached by the validate gate (superseding sweep) and the
    # `--decision list` CLI. A FIFO/symlink->/dev/zero decisions.md would block
    # read_text forever; it must skip a non-regular ledger (fail-soft []).
    import os
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(ledger)
    assert _bounded(lambda: dec_ledger.list_decisions(tmp_path)) == []


def test_alloc_rejects_fifo_ledger_without_hanging(tmp_path):
    # The --add allocator reads the ledger inside its flock; a FIFO ledger would
    # block read_text forever. It must raise a clean error (not hang) so the CLI
    # exits instead of freezing.
    import os
    import pytest
    ledger = _ledger_path(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(ledger)
    with pytest.raises(ValueError):
        _bounded(lambda: dec_ledger.alloc(tmp_path, status="active", affects=["X"]))
