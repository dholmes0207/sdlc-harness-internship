#!/usr/bin/env python3
"""plan_approval.py — write the plan-approval artifact (personal-first SLIM).

No roster, no quorum, no role rule: self-approval is deliberate anti-drift
discipline, not an anti-fraud check (actor strings are env-derived and
spoofable by design, so a role gate would prove nothing). The real guard is
plan_hash, which binds the approval to the exact plan body.

plan_hash pins the plan's NORMALIZED content: YAML frontmatter is stripped
from every file and the `## Phases` section is stripped from plan.md before
hashing. Those are exactly the two regions the cook workflow legitimately
mutates after approval (status flips, phase table updates) — hashing them
verbatim would go stale on every run and train reviewers to rubber-stamp.
The one exception is the `plan-graph.yaml` sidecar: it is hashed RAW (no
frontmatter strip), because a sidecar opening with `---` would otherwise be
swallowed to "" by the frontmatter rule and drop out of the hash entirely.
Trade-off, on purpose: status metadata is not drift-guarded; the body (the
thing approval is about) is. Any other edit ⇒ re-approve.

The artifact is the only in-session write path for plans/*/artifacts/
plan-approval.json (the file sits on the write-guard list in installed
repos), and this CLI refuses to write on a bad verdict, missing sidecar,
empty rationale, or an unresolvable author.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import fs_guard  # noqa: E402
import harness_paths  # noqa: E402
import plan_status  # noqa: E402

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.append(str(_HOOKS_DIR))
import hook_runtime  # noqa: E402
import trace_log  # noqa: E402

SCHEMA = "plan-approval/v1"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?^(?:---|\.\.\.)\s*$\n?",
                             re.MULTILINE | re.DOTALL)
_PHASES_SECTION_RE = re.compile(r"(?ms)^## Phases\s*$.*?(?=^## |\Z)")


# -------------------------------------------------------------- normalize ---

def normalize_actor(actor) -> str:
    """`user:<u>/agent:<a>` → `user:<u>`, then casefold + strip — the agent
    suffix is a persona of the same person, and so are case/whitespace variants
    of the same identity (a git email is commonly mixed-case). Comparing the
    normalized form is what makes `reviewer != author` hold against a casing
    difference, so the self-review block cannot be sidestepped by approving from
    `BOB@x.com` what `bob@x.com` authored. (the normalization extends bare
    agent-suffix stripping with case + whitespace insensitivity.)"""
    # .strip('"').strip("'") closes F1: a plan author read from `author:`
    # frontmatter arrives WITH surrounding quotes, and a quoted form must
    # normalize equal to the bare actor or a self-review/self-override slips.
    return str(actor).split("/agent:")[0].strip().strip('"').strip("'").strip().casefold()


# ------------------------------------------------------- normalized hashes ---

def _plan_files(plan_dir: Path):
    plan_dir = Path(plan_dir)
    files = [plan_dir / "plan.md"]
    files += sorted(plan_dir.glob("phase-*.md"))
    # Phase files at the plan-dir root (flat) AND under phases/ (scaffold layout);
    # both fold into the hash keyed by basename so a phases/-layout plan cannot slip
    # phase edits past approval, and a flat->phases/ migration of identical content
    # does not spuriously trip drift.
    files += sorted(plan_dir.glob("phases/phase-*.md"))
    # Machine-readable phase-DAG sidecar: hardcode the name so a mutable
    # frontmatter `phase_graph:` marker cannot redirect which file the hash covers.
    side = plan_dir / "plan-graph.yaml"
    if side.is_file():
        files.append(side)
    return [f for f in files if f.is_file()]


def _normalized_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.name == "plan-graph.yaml":
        # Raw-hash the sidecar: it has no mutable status region to strip, and a
        # sidecar that opens with `---` would be swallowed to "" by the frontmatter
        # strip and collide on the empty-sha digest. One special-case here covers
        # both plan_hash and file_hashes (both route through this function).
        return text
    text = _FRONTMATTER_RE.sub("", text, count=1)
    if path.name == "plan.md":
        # Only plan.md owns a legitimately-mutating `## Phases` section;
        # the same heading in a phase file is body and stays pinned.
        text = _PHASES_SECTION_RE.sub("", text, count=1)
    return text


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

_APPROVAL_EXTS = (".yaml", ".json")


def _existing_primary(plan_dir):
    """The on-disk primary approval (plan-approval.yaml preferred, .json legacy)
    or None when neither exists yet."""
    art = Path(plan_dir) / "artifacts"
    for ext in _APPROVAL_EXTS:
        cand = art / ("plan-approval%s" % ext)
        if cand.is_file():
            return cand
    return None


def _read_record(path) -> dict:
    """Parse an approval record by extension. Raises ValueError/OSError on a bad
    read so the caller's try/except treats it as an unknown prior reviewer."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".yaml":
        import yaml
        rec = yaml.safe_load(text)
    else:
        rec = json.loads(text)
    if not isinstance(rec, dict):
        raise ValueError("approval record is not a mapping: %s" % path)
    return rec


def _dump_record(rec: dict, path) -> None:
    """Write an approval record via the shared gate-artifact writer: run_seq stamp (D1)
    from the orchestrator-exported env + atomic same-dir .tmp + os.replace. Format is
    implied by the extension; a dev without an orchestrator gets run_seq:null."""
    import artifact_io
    artifact_io.stamp_and_write(Path(path), rec)


def file_hashes(plan_dir) -> dict:
    """filename → sha256-12hex of that file's normalized content. Lets the
    gate name exactly WHICH file drifted after approval."""
    return {f.name: _digest(_normalized_text(f)) for f in _plan_files(plan_dir)}


def plan_hash(plan_dir) -> str:
    """sha256-12hex over the whole normalized plan dir (plan.md + phase-*.md,
    sorted by name, filename-delimited so renames change the hash too)."""
    h = hashlib.sha256()
    for f in _plan_files(plan_dir):
        h.update(f.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(_normalized_text(f).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


# ----------------------------------------------------------------- author ---

_AUTHOR_FM_RE = re.compile(r"^author:\s*(.+?)\s*$", re.MULTILINE)


def _author_from_trace(plan_name) -> "str | None":
    """Best-effort: actor of a plan-creation trace event for this plan."""
    try:
        trace_dir = harness_paths.trace_dir()
        for f in sorted(trace_dir.glob("trace-*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("event") == "plan_created" \
                        and rec.get("target") == plan_name:
                    return rec.get("actor")
    except OSError:
        pass
    return None


def _resolve_author(plan_dir: Path) -> "str | None":
    """Trace event (if a plan_created event exists) → plan.md frontmatter
    `author:` → None (caller must demand --author; guessing an author would
    let the role check pass against the wrong person)."""
    author = _author_from_trace(plan_dir.name)
    if author:
        return author
    pm = plan_dir / "plan.md"
    try:
        fm = _FRONTMATTER_RE.match(pm.read_text(encoding="utf-8"))
    except OSError:
        return None
    if fm:
        m = _AUTHOR_FM_RE.search(fm.group(0))
        if m:
            return m.group(1)
    return None


# ------------------------------------------------------------------ write ---

def write_approval(plan_dir, verdict, rationale, author=None,
                   reviewer=None) -> dict:
    """Write plans/<plan>/artifacts/plan-approval.yaml (legacy .json primaries
    refresh in place). Personal-first SLIM: no roster, no quorum, no role rule —
    self-approval is deliberate discipline (anti-drift), not an anti-fraud check.
    The APPROVED verdict still requires the plan-graph sidecar; the plan_hash still
    binds the approval to the exact plan body. Refuses (no write) on a bad verdict,
    missing sidecar, empty rationale, or unresolvable author."""
    plan_dir = Path(plan_dir)
    # Case-insensitive verdict: a valid intent in any case (approved / ApprOvEd /
    # " approved ") normalizes to the canonical upper form, so it is never a
    # false-positive reject. Garbage still fails the membership check below.
    verdict = str(verdict).strip().upper()
    if not (plan_dir / "plan.md").is_file():
        return {"ok": False,
                "error": "no plan.md in %s — point --plan at a plan dir or its "
                         "plan.md" % plan_dir}
    # The phase-DAG sidecar is a mandatory plan artifact (hs:plan step 5). An
    # APPROVED verdict is refused without it — presence gate, fail-closed; a
    # REJECTED verdict needs no sidecar (you can reject an incomplete plan).
    if verdict == "APPROVED" and not (plan_dir / "plan-graph.yaml").is_file():
        return {"ok": False,
                "error": "no plan-graph.yaml in %s — the phase-DAG sidecar is "
                         "mandatory for approval (author it in hs:plan step 5: "
                         "edges + per-phase file ownership), then re-approve"
                         % plan_dir}
    if verdict not in ("APPROVED", "REJECTED"):
        return {"ok": False,
                "error": "verdict must be APPROVED or REJECTED (got %r)"
                         % verdict}
    if not (rationale or "").strip():
        return {"ok": False, "error": "a non-empty --rationale is required"}

    reviewer = reviewer or hook_runtime.resolve_actor()
    author = (author or "").strip() or _resolve_author(plan_dir)
    if not author:
        return {"ok": False,
                "error": "cannot resolve the plan author (no creation trace "
                         "event, no `author:` frontmatter in plan.md) — pass "
                         "--author user:<who> explicitly; refusing to write "
                         "an approval with an empty author"}

    root = harness_paths.root()

    rec = {
        "schema": SCHEMA,
        "plan": plan_dir.name,
        "plan_hash": plan_hash(plan_dir),
        "file_hashes": file_hashes(plan_dir),
        "author": author,
        "reviewer": reviewer,
        "verdict": verdict,
        "rationale": rationale,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    # Personal-first SLIM: one self-approval per plan. Refresh the primary in
    # place (keeping a legacy .json format) or write plan-approval.yaml. No
    # co-sign fan-out, no reviewer-hash files — quorum/roster are gone.
    art = plan_dir / "artifacts"
    existing_primary = _existing_primary(plan_dir)
    target = existing_primary if existing_primary is not None else art / "plan-approval.yaml"
    try:
        fs_guard.assert_under(target, "plans", root=root)
    except fs_guard.FenceError as e:
        # Match write_approval's contract: every failure returns a structured
        # error, never an uncaught crash.
        return {"ok": False, "error": str(e)}
    target.parent.mkdir(parents=True, exist_ok=True)
    _dump_record(rec, target)
    trace_log.append_event("plan_approval", "approval_written",
                           actor=reviewer, target=plan_dir.name,
                           status=verdict,
                           note="plan_hash=%s author=%s" % (
                               rec["plan_hash"], author))
    # Reflect the verdict into the plan's status: an APPROVED plan moves
    # pending -> approved so the board shows it as reviewed-and-ready rather than
    # lumped with un-reviewed pending plans. Ordering matters — the flip happens
    # AFTER the artifact is written, and plan_hash strips frontmatter, so flipping
    # status never invalidates the approval just recorded. error_on_other=False
    # makes APPROVED on an in_progress/approved/completed plan a benign idempotent
    # no-op; only `pending` flips. The flip is secondary to the approval: if it
    # fails (containment, missing status line) the artifact still stands, so a
    # flip error is logged and swallowed rather than failing the approval.
    if verdict == "APPROVED":
        try:
            flip = plan_status.flip_status(
                plan_dir, allowed_from={"pending"}, to="approved",
                error_on_other=False, root=root)
            if not flip.ok:
                sys.stderr.write(
                    "[plan_approval] approval written but status flip skipped: "
                    "%s\n" % flip.message)
        except Exception as e:  # noqa: BLE001 — flip is secondary to the approval
            sys.stderr.write(
                "[plan_approval] approval written but status flip errored: %s\n"
                % e)
    return {"ok": True, "artifact": str(target), "record": rec}


# -------------------------------------------------------------------- CLI ---

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write the plan-approval artifact "
                    "(plans/<plan>/artifacts/plan-approval.yaml).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "REQUIRED every call: --plan, --verdict, --rationale.\n"
            "CONDITIONALLY REQUIRED: --author \u2014 needed ONLY when the author\n"
            "  cannot be resolved automatically. Resolution order:\n"
            "    1. a `plan_created` trace event for this plan (auto), else\n"
            "    2. an `author:` line in plan.md frontmatter (auto), else\n"
            "    3. you MUST pass --author user:<id> (the call is refused\n"
            "       otherwise \u2014 an empty author is never written).\n"
            "  Source for --author: the plan's own author, as `user:<id>`\n"
            "  (e.g. user:you@example.com); read plan.md frontmatter `author:`\n"
            "  or use the current HARNESS_USER identity.\n"
            "NOT a flag: `reviewer` is auto-resolved from the current actor;\n"
            "  the plan-graph sidecar (plan-graph.yaml) must exist for APPROVED."
        ))
    ap.add_argument("--plan", required=True,
                    help="[REQUIRED] the plan: a plan dir, a path to its plan.md "
                         "(any file in the dir resolves to the dir), or a bare "
                         "name under plans/")
    ap.add_argument("--verdict", required=True,
                    type=lambda s: s.strip().upper(),
                    choices=("APPROVED", "REJECTED"),
                    help="[REQUIRED] APPROVED or REJECTED (case-insensitive: "
                         "approved / ApprOvEd / REJECTED all accepted)")
    ap.add_argument("--rationale", required=True,
                    help="[REQUIRED] one-line reason for the verdict")
    ap.add_argument("--author", default=None,
                    help="[CONDITIONAL] the PLAN's author as user:<id>. Required "
                         "ONLY when no `plan_created` trace event and no plan.md "
                         "`author:` frontmatter resolve one. Source: plan.md "
                         "`author:` or the HARNESS_USER identity.")
    args = ap.parse_args(argv)

    # --plan accepts a plan dir, a path to its plan.md (any file inside the plan
    # dir), or a bare name under plans/ — resolve all three to the plan directory.
    plan_arg = Path(args.plan)
    if plan_arg.is_file():
        plan_dir = plan_arg.parent
    elif plan_arg.is_dir():
        plan_dir = plan_arg
    else:
        under_plans = harness_paths.root() / "plans" / args.plan
        plan_dir = under_plans if under_plans.is_dir() else plan_arg

    result = write_approval(plan_dir, verdict=args.verdict,
                            rationale=args.rationale, author=args.author)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
