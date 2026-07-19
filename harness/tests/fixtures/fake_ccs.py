#!/usr/bin/env python3
"""fake_ccs — a polluted-stdout stand-in for `ccs <provider> -p ... --output-format
json`, wired via HARNESS_CCS_CMD. Mirrors the real shape probed live 260711-1459:
stdout is NEVER a single clean JSON document — it is banner ANSI art + a human
table + three newline-delimited JSON stream objects (system, assistant, result).
A fake that printed clean JSON would pass every unit test while the real `ccs`
crashes a `json.loads(stdout)` parser (the exact trap this fixture exists to
catch — see harness/tests/test_partner_transport.py).

FAKE_CCS_MODE env selects a variant: "no_result" (drop the result record),
"nonzero" (exit 1 with stderr), "timeout" (sleep past any reasonable deadline),
"garbage" (emit non-JSON stdout only), "no_write" (phase 5 — answer OK but
touch no file, simulating a write turn that landed nothing). Default emits
the full polluted-but-complete stream and, for a write-mode invocation
(no --permission-mode reached this fake — see below), lands a file in cwd.
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("provider")
    ap.add_argument("-p", "--print", dest="prompt", default="")
    ap.add_argument("--output-format", dest="output_format", default=None)
    ap.add_argument("--permission-mode", dest="permission_mode", default=None)
    args, _unknown = ap.parse_known_args(argv)

    mode = os.environ.get("FAKE_CCS_MODE", "ok")

    if mode == "timeout":
        time.sleep(30)
        return 0

    if mode == "nonzero":
        sys.stderr.write("ccs: error: fake failure\n")
        return 1

    if mode == "garbage":
        sys.stdout.write("not json at all, just plain garbage text\n")
        return 0

    if mode == "transient_once":
        # SF-3 retry-vs-partial-write simulation: the FIRST invocation (no
        # marker file yet) drops a STRAY file into cwd then fails with a
        # transient marker; the SECOND invocation (marker present) behaves
        # normally. Proves a retry that does not reset its worktree between
        # attempts would fold attempt-1's stray file into the final diff.
        marker = os.environ.get("FAKE_CCS_MARKER")
        if marker and not Path(marker).exists():
            Path(marker).write_text("attempt-1\n", encoding="utf-8")
            Path("stray.txt").write_text("stray from failed attempt 1\n",
                                         encoding="utf-8")
            sys.stderr.write("ccs: error: rate_limit exceeded (transient)\n")
            return 1
        # Second invocation onward: fall through to the normal ok path below.

    # Phase 5 (write path): the transport drops --permission-mode entirely for
    # a write turn (mode="write" is not in partner_transport._ADVISORY_MODES)
    # — ITS ABSENCE is the on-the-wire signal a write-mode invocation carries,
    # the same signal a real accept-edits `ccs -p` call would send. Land a
    # file in cwd so the worktree diff captured around this call is
    # non-empty. "no_write" simulates ccs answering OK but writing nothing
    # (the empty-diff-raise scenario).
    if args.permission_mode is None and mode != "no_write":
        out_name = os.environ.get("FAKE_CCS_WRITE_FILE", "ccs_write_out.txt")
        Path(out_name).write_text(
            "written by fake ccs: %s\n" % args.prompt, encoding="utf-8")
        # SF-2 escape simulation: a write-mode turn that ALSO touches a path
        # OUTSIDE the caller's cwd/worktree (an absolute path, e.g. repo_root),
        # mirroring a delegated session's tools writing beyond the worktree
        # jail. The normal cwd write above still lands, so the worktree diff
        # stays non-empty — only the escape check should trip.
        escape_path = os.environ.get("FAKE_CCS_ESCAPE_FILE")
        if escape_path:
            Path(escape_path).write_text(
                "escaped write by fake ccs: %s\n" % args.prompt, encoding="utf-8")

    # Banner + table pollution — real ccs never emits a clean single JSON blob
    # here; a stdout starting with `{` would be the wrong shape to fake.
    sys.stdout.write("╭─ Delegated ─╮\n")
    sys.stdout.write("| provider | model | mode |\n")
    sys.stdout.write("| %s | fake-model-1 | %s |\n" % (args.provider, args.permission_mode))

    session_id = str(uuid.uuid4())
    system_rec = {"type": "system", "permissionMode": args.permission_mode,
                  "session_id": session_id, "model": "fake-model-1"}
    assistant_rec = {"type": "assistant",
                      "message": {"content": [{"type": "text", "text": args.prompt}]}}
    result_rec = {"type": "result", "total_cost_usd": 0.0042,
                  "usage": {"input_tokens": 12, "output_tokens": 34},
                  "result": "fake answer: %s" % args.prompt}

    if mode == "no_result_key":
        # A type=="result" record that IS present but is missing its own
        # "result" text field — distinct from "no_result" (record absent
        # entirely). N-6: this must not be read as an empty-but-ok answer.
        del result_rec["result"]

    sys.stdout.write(json.dumps(system_rec) + "\n")
    sys.stdout.write(json.dumps(assistant_rec) + "\n")
    if mode != "no_result":
        sys.stdout.write(json.dumps(result_rec) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
