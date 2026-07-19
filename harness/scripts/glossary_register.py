#!/usr/bin/env python3
"""glossary_register — the Glossary Register: the authoritative home for the
harness's shared vocabulary (one settled term replaces a paragraph of
re-explanation, so naming stays consistent across the tree and across the team).

Mirrors decision_register's machinery with ONE deliberate difference: a term is
keyed by its own name, not a monotonic id. A glossary is looked up by name and
terms never supersede, so there is no `GL-<n>` to allocate — the term IS the key.

Script-vs-LLM split: this script owns the deterministic structural work — parse,
list, render the markdown view, drift-check, migrate, and append a new term
without overwriting prior records. The caller owns the human prose (definition,
forbidden wording) passed on the CLI.

Storage (dual-mode): the SSOT is docs/glossary.yaml when present — a YAML list of
records (load -> mutate -> dump), with docs/GLOSSARY.md rendered as a secondary
human view. Absent glossary.yaml, the legacy markdown TABLE is the source (a
deployer repo that has not migrated yet), and --migrate converts it. Both modes
resolve every write through the fs_guard "docs" zone, so a register write can
never escape the docs boundary.

Record schema (per term):
    term:        the key (unique, case-sensitive). Required.
    definition:  one-paragraph prose. Required.
    forbidden:   list[str] of banned framings (may be empty []).
    backing:     list[str] of DEC ids / test files / doc anchors (may be []).
    actor, ts:   machine-written (resolve_actor + UTC isoformat).

CLI:
    glossary_register.py --root <dir> --add --term T --definition D \\
        [--forbidden F ...] [--backing B ...]
    glossary_register.py --root <dir> --list      # JSON: {"terms": [...]}
    glossary_register.py --root <dir> --render [--force]   # write the md view
    glossary_register.py --root <dir> --check     # drift-check, exit 1 on drift
    glossary_register.py --root <dir> --migrate [--force] # legacy md -> yaml
"""

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import fs_guard  # noqa: E402
from register_store import atomic_write, register_lock  # noqa: E402

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.append(str(_HOOKS_DIR))
import hook_runtime  # noqa: E402

# Windows consoles may default to a legacy codepage; UTF-8 JSON output must not
# crash there. reconfigure exists on 3.7+; guard for exotic stdouts.
if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

# Canonical record field order in the YAML SSOT.
_REC_FIELDS = ("term", "definition", "forbidden", "backing", "actor", "ts")

# The rendered view's no-clobber / drift marker (mirrors render_standards +
# decision_register): its presence proves the file is a prior render, so a
# hand-authored view is never silently overwritten by a render of the SSOT.
GENERATED_MARKER = (
    "<!-- generated from docs/glossary.yaml by glossary_register.py — edit the "
    "YAML SSOT (or run glossary_register.py --add), not this file -->"
)

# The fixed prose header of the rendered view (English by convention — the
# instruction surface stays English even when generated reports are not). The
# coin instruction points at the register, NOT at hand-editing this file.
_VIEW_HEADER = """---
id: harness.glossary
type: glossary
status: stable
version: 3.1.0
owner: maintainer
---

# Glossary — SDLC Harness shared language

{marker}

Canonical vocabulary for the harness. Agents **read this before naming** \
variables, files, hooks, or prose, so one settled term replaces a paragraph of \
re-explanation and naming stays consistent across the tree. English by \
convention — the instruction surface (skills, rules, agents, CLAUDE.md) is \
English even when generated reports are not (see `harness/data/output.yaml`).

**This file is a generated VIEW of `docs/glossary.yaml`.** Do NOT edit it by \
hand — a render overwrites it. To coin a new load-bearing term, run \
`glossary_register.py --add` (human-approved) or edit the YAML SSOT, then \
re-render. `hs:plan` and `hs:discover` read the glossary before naming. The \
`Forbidden wording` column is the human-readable side of the bans that \
`harness/tests/test_bug_class_invariants.py` enforces over `harness/`.

| Term | Definition | Forbidden wording | Backing |
|---|---|---|---|
"""


class GlossaryError(ValueError):
    """Raised on a shape/uniqueness violation (surfaced as a JSON finding by
    the CLI; raised directly for library callers)."""


def _yaml_path(root) -> Path:
    """The pure-YAML SSOT path. When this file exists the register is in YAML
    mode: CRUD on a YAML list, GLOSSARY.md rendered as a secondary view."""
    return Path(root) / "docs" / "glossary.yaml"


def _view_path(root) -> Path:
    """The markdown view path. In YAML mode this is the rendered secondary view;
    in legacy mode it is the source table."""
    return Path(root) / "docs" / "GLOSSARY.md"


def _uses_yaml(root) -> bool:
    """True when the register is a pure-YAML SSOT (glossary.yaml present);
    otherwise the legacy markdown table is the source (un-migrated repo)."""
    return _yaml_path(root).is_file()


def _lock_path(root) -> Path:
    return Path(root) / "docs" / ".glossary_register.lock"


def _as_list(val) -> List[str]:
    """Coerce a record's forbidden/backing field to a clean list[str]. A bare
    scalar (legacy single value) becomes a one-element list; None/'' -> []."""
    if val in (None, ""):
        return []
    if isinstance(val, (list, tuple)):
        return [str(v).strip() for v in val if str(v).strip()]
    return [str(val).strip()]


def _norm_record(raw: Dict[str, Any]):
    """Coerce a raw YAML mapping to a canonical record, or None when the term
    key is missing/empty (skipped fail-soft — one bad row never sinks --list)."""
    term = str(raw.get("term", "")).strip()
    if not term:
        return None
    return {
        "term": term,
        "definition": str(raw.get("definition", "") or "").strip(),
        "forbidden": _as_list(raw.get("forbidden")),
        "backing": _as_list(raw.get("backing")),
        "actor": str(raw.get("actor", "") or "").strip(),
        "ts": str(raw.get("ts", "") or "").strip(),
    }


def _load_yaml_raw(root) -> List[Dict[str, Any]]:
    """Records from the YAML SSOT. A whole-file parse failure yields [] (fail-
    soft) — a corrupt SSOT never crashes a read."""
    try:
        data = yaml.safe_load(_yaml_path(root).read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for raw in data:
        if isinstance(raw, dict):
            rec = _norm_record(raw)
            if rec is not None:
                out.append(rec)
    return out


# A legacy markdown table row: `| term | definition | forbidden | backing |`.
# Cells are split on ` | ` (pipe WITH surrounding spaces), not a bare `|`, so a
# pipe embedded inside a cell (e.g. `<n|id>` with no spaces) survives the split
# instead of forging a 5th column and dropping the row.
def _parse_md_table(root) -> List[Dict[str, Any]]:
    """Legacy markdown source: parse the 4-column table into records. The
    header row and the `|---|` separator are skipped. forbidden is kept as a
    single verbatim element (prose-heavy, never split); backing is split on `;`
    (mirrors how the cell was historically authored)."""
    try:
        text = _view_path(root).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return []
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cells = [c.strip() for c in s[1:-1].split(" | ")]
        if len(cells) != 4:
            continue
        term = cells[0]
        if term in ("Term", "") or set(term) <= {"-", ":"}:
            continue  # header row or the |---| separator
        forbidden = [cells[2]] if cells[2] else []
        backing = [b.strip() for b in cells[3].split(";") if b.strip()]
        out.append({"term": term, "definition": cells[1],
                    "forbidden": forbidden, "backing": backing,
                    "actor": "", "ts": ""})
    return out


def parse_glossary(root) -> List[Dict[str, Any]]:
    """Every term record. Dispatches: YAML SSOT when glossary.yaml exists, else
    the legacy markdown table. Missing source -> empty list."""
    if _uses_yaml(root):
        return _load_yaml_raw(root)
    return _parse_md_table(root)


def list_terms(root) -> List[Dict[str, Any]]:
    """All term records (public read door — consumers read this, never the
    rendered markdown, when they need the structured vocabulary)."""
    return parse_glossary(root)


def _cell(text: str) -> str:
    """Make caller text safe inside a single markdown table cell: collapse
    newlines (a literal break would split the table into a forged row/heading)
    and escape pipes (a raw `|` opens a phantom column). This is the table
    analogue of the register-fence escape — injection cannot forge a row."""
    flat = re.sub(r"[\r\n]+", " ", text or "").strip()
    return flat.replace("|", r"\|")


def render_md(records: List[Dict[str, Any]]) -> str:
    """Render the term records to the markdown view. Deterministic (sorted by
    term, case-insensitive), 4-column table, forbidden joined with ` / ` and
    backing with `; ` (reproducing the historical cell shape), every cell
    injection-escaped so the view can never be re-parsed into a phantom row."""
    rows = []
    for r in sorted(records, key=lambda x: x["term"].lower()):
        forbidden = " / ".join(_as_list(r.get("forbidden")))
        backing = "; ".join(_as_list(r.get("backing")))
        rows.append("| %s | %s | %s | %s |" % (
            _cell(r["term"]), _cell(r.get("definition", "")),
            _cell(forbidden), _cell(backing)))
    body = _VIEW_HEADER.format(marker=GENERATED_MARKER)
    return body + "\n".join(rows) + "\n"


def _dump_yaml(root, records: List[Dict[str, Any]]) -> Path:
    """Write the YAML SSOT atomically through the docs fs_guard zone. Returns
    the SSOT path. Does NOT render the view — callers that want the view in
    sync call render() (gated by no-clobber)."""
    yp = _yaml_path(root)
    fs_guard.assert_under(yp, "docs", root=root)
    yp.parent.mkdir(parents=True, exist_ok=True)
    payload = [{k: r.get(k, "" if k in ("term", "definition", "actor", "ts")
                         else []) for k in _REC_FIELDS} for r in records]
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=4096)
    atomic_write(yp, text)
    return yp


def _no_clobber_ok(root, force: bool) -> bool:
    """True when it is safe to (over)write the markdown view: the view does not
    exist, already carries the GENERATED_MARKER (a prior render), or --force is
    set. A marker-less view is hand-authored — refuse to silently replace it."""
    if force:
        return True
    view = _view_path(root)
    if not view.exists():
        return True
    try:
        return GENERATED_MARKER in view.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def render(root, force: bool = False) -> Path:
    """Render the SSOT to docs/GLOSSARY.md (through the docs fs_guard zone),
    refusing to clobber a hand-authored (marker-less) view unless force."""
    if not _no_clobber_ok(root, force):
        raise GlossaryError(
            "%s exists and is not a generated view (no marker); pass --force "
            "to overwrite" % _view_path(root))
    view = _view_path(root)
    fs_guard.assert_under(view, "docs", root=root)
    view.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(view, render_md(parse_glossary(root)))
    return view


def check_drift(root) -> bool:
    """True when the rendered view is OUT OF SYNC with the SSOT (or absent).
    The drift gate: a test runs render in-memory and compares to the committed
    GLOSSARY.md — any difference is drift."""
    rendered = render_md(parse_glossary(root))
    try:
        return _view_path(root).read_text(encoding="utf-8") != rendered
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return True


def add_term(root, term: str, definition: str,
             forbidden=None, backing=None) -> Path:
    """Append one term to the SSOT. Append-only: prior records untouched.
    Refuses a duplicate term (a glossary entry is updated by editing the YAML,
    not by a second --add that would silently shadow the first). Returns the
    SSOT path. Runs inside the register lock so concurrent appends on one
    machine cannot drop each other's records."""
    term = re.sub(r"[\r\n]+", " ", term or "").strip()
    if not term:
        raise GlossaryError("a glossary entry needs a non-empty term")
    if not (definition or "").strip():
        raise GlossaryError("term %r needs a non-empty definition" % term)
    with register_lock(_lock_path(root)):
        # In a fresh deployer repo the SSOT may not exist yet — seed from the
        # legacy table so an --add never silently drops the migrated terms.
        records = _load_yaml_raw(root) if _uses_yaml(root) \
            else _parse_md_table(root)
        if any(r["term"] == term for r in records):
            raise GlossaryError(
                "term %r already in the glossary; entries are append-only — "
                "edit docs/glossary.yaml to revise it" % term)
        actor = hook_runtime.resolve_actor()
        ts = dt.datetime.now(dt.timezone.utc).isoformat()
        records.append({
            "term": term,
            "definition": re.sub(r"[\r\n]+", " ", definition).strip(),
            "forbidden": _as_list(forbidden),
            "backing": _as_list(backing),
            "actor": actor,
            "ts": ts,
        })
        return _dump_yaml(root, records)


def migrate(root, force: bool = False) -> Dict[str, Any]:
    """One-shot: convert a legacy markdown table to the YAML SSOT. Parses the
    existing GLOSSARY.md (preserving every term + forbidden + backing), writes
    glossary.yaml, and re-renders GLOSSARY.md as the secondary view. Idempotent
    once glossary.yaml exists (re-running re-dumps from the SSOT). The re-render
    honours no-clobber — the FIRST migration of a hand-authored view needs
    --force (it has been parsed already, so no data is lost)."""
    with register_lock(_lock_path(root)):
        records = parse_glossary(root)
        _dump_yaml(root, records)
        if not _no_clobber_ok(root, force):
            raise GlossaryError(
                "%s exists and is not a generated view (no marker); pass "
                "--force to convert it" % _view_path(root))
        view = _view_path(root)
        fs_guard.assert_under(view, "docs", root=root)
        atomic_write(view, render_md(records))
    return {"migrated": len(records), "yaml": str(_yaml_path(root))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--add", action="store_true",
                      help="append a term record")
    mode.add_argument("--list", action="store_true",
                      help="print all terms as JSON")
    mode.add_argument("--render", action="store_true",
                      help="render the markdown view from the SSOT")
    mode.add_argument("--check", action="store_true",
                      help="drift-check the view against the SSOT (exit 1)")
    mode.add_argument("--migrate", action="store_true",
                      help="convert a legacy GLOSSARY.md table to the SSOT")
    ap.add_argument("--term")
    ap.add_argument("--definition")
    ap.add_argument("--forbidden", nargs="*", default=[])
    ap.add_argument("--backing", nargs="*", default=[])
    ap.add_argument("--force", action="store_true",
                    help="override the markdown view no-clobber guard")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    try:
        if args.list:
            print(json.dumps({"terms": list_terms(root)}, indent=2,
                             ensure_ascii=False))
            return 0
        if args.check:
            if check_drift(root):
                sys.stderr.write(
                    "drift: docs/GLOSSARY.md is out of date — re-render from "
                    "the SSOT (glossary_register.py --render)\n")
                return 1
            return 0
        if args.migrate:
            print(json.dumps(migrate(root, force=args.force),
                             ensure_ascii=False))
            return 0
        if args.render:
            print(str(render(root, force=args.force)))
            return 0
        # --add
        path = add_term(root, term=args.term or "",
                        definition=args.definition or "",
                        forbidden=args.forbidden, backing=args.backing)
        rel = str(Path(path).relative_to(root))
        print(json.dumps({"term": args.term, "written": True, "ssot": rel},
                         ensure_ascii=False))
        return 0
    except GlossaryError as exc:
        # Render no-clobber is an operator guard, not bad input: surface it on
        # stderr with exit 2 (mirrors render_standards) so a script can react.
        if args.render or args.migrate:
            sys.stderr.write("error: %s\n" % exc)
            return 2
        # Analytical-script contract: bad --add input -> JSON finding, exit 0.
        print(json.dumps({"error": "invalid_input", "message": str(exc),
                          "written": False}, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 — surface as finding, never traceback
        print(json.dumps({"error": "invalid_input", "message": str(exc),
                          "written": False}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
