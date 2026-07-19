#!/usr/bin/env python3
"""fake_agy — a close stand-in for `agy` print mode, wired via HARNESS_AGY_CMD.

Mirrors the real CLI shape probed live against agy 0.x: the prompt is the VALUE of
-p/--print/--prompt (it must come after --model), and --model/--log-file/
--conversation/--dangerously-skip-permissions are tolerated. Control flags let a
test force an exit code + a stderr marker (transient classification) and write a
conversation id line to --log-file (P6 id-capture). stdout carries ONLY the answer
text, clean — the live probe confirmed the real agy prints clean text with no
banner on stdout. By default the prompt is echoed back (deterministic).
"""
import argparse
import os
import re
import sys
import uuid
from pathlib import Path

# The write-target directory the sandbox path injects into the prompt (agy ignores
# cwd, so the contract is an ABSOLUTE path in the prompt). Kept in sync with
# gemini_companion._SANDBOX_WRITE_MARKER.
# Capture to end-of-line (not \S+) so a worktree abspath containing spaces is not
# truncated (the marker is followed by a newline in the injected prompt).
_WRITE_DIR_RE = re.compile(r"\[sandbox-write-dir\]\s+(.+)")
# SSH_* leaking into agy's env breaks its file-token auth (probed) — the transport
# strips these; the fake fails auth if any survive (proves the strip).
_SSH_VARS = ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION", "SSH_AUTH_SOCK", "SSH_AGENT_PID")


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("-p", "--print", "--prompt", dest="prompt", default="")
    ap.add_argument("--model", default=None)
    ap.add_argument("--log-file", dest="log_file", default=None)
    ap.add_argument("--conversation", default=None)
    ap.add_argument("--dangerously-skip-permissions", dest="skip_perms",
                    action="store_true")
    # control knobs (ride in HARNESS_AGY_CMD; the real agy has no such flags)
    ap.add_argument("--exit-code", type=int, default=0)
    ap.add_argument("--stderr-marker", default=None)
    ap.add_argument("--emit", default=None,
                    help="stdout to emit instead of echoing the prompt")
    ap.add_argument("--no-uuid-log", action="store_true",
                    help="write a log WITHOUT a conversation UUID (fail-safe test)")
    ap.add_argument("--fork-on-resume", action="store_true",
                    help="on --conversation, log a DIFFERENT fresh id (simulate agy "
                         "silently forking an unknown/expired conversation)")
    ap.add_argument("--fail-on-ssh", action="store_true",
                    help="exit non-zero if any SSH_* env survives (proves the strip)")
    ap.add_argument("--write-elsewhere", default=None,
                    help="land ONLY in THIS dir, never the worktree (simulate agy "
                         "writing to scratch OUTSIDE the repo → empty worktree diff)")
    ap.add_argument("--escape-to", default=None,
                    help="ALSO write into THIS in-repo dir on top of the worktree "
                         "(simulate an in-repo escape the escape-scan must catch)")
    args, _unknown = ap.parse_known_args(argv)

    def _write_file(target):
        try:
            p = Path(target) / "agy_out.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("agy wrote this\n", encoding="utf-8")
        except OSError:
            pass

    # Auth dies if SSH_* leaked through (only when the transport forgot to strip).
    if args.fail_on_ssh and any(os.environ.get(v) for v in _SSH_VARS):
        sys.stderr.write("agy: error: file-token auth failed (SSH_* in env)\n")
        return 1

    # Write path: only in a write turn (--dangerously-skip-permissions, the flag the
    # transport adds for yolo/write), mirroring real agy needing explicit permission.
    if args.skip_perms:
        if args.write_elsewhere:
            _write_file(args.write_elsewhere)  # only outside the worktree (F4 sim)
        else:
            m = _WRITE_DIR_RE.search(args.prompt or "")
            if m:
                _write_file(m.group(1).strip())  # the injected worktree abspath
            if args.escape_to:
                _write_file(args.escape_to)    # additional in-repo escape

    # Write a conversation-id line to the log file so P6 can recover the UUID by
    # parsing --log-file (the real agy puts a stable UUID v4 there, verified). A
    # resume (--conversation) keeps the same id. --no-uuid-log omits it to exercise
    # the recall-miss fail-safe.
    if args.log_file:
        # Normally a resume keeps the requested id; --fork-on-resume logs a fresh
        # DIFFERENT id to mimic agy silently starting over on an unknown id.
        if args.conversation and not args.fork_on_resume:
            cid = args.conversation
        else:
            cid = str(uuid.uuid4())
        try:
            with open(args.log_file, "w", encoding="utf-8") as fh:
                if args.no_uuid_log:
                    fh.write("time=… level=INFO conversation started (no id logged)\n")
                else:
                    fh.write("time=… level=INFO conversation id: %s started\n" % cid)
        except OSError:
            pass

    if args.stderr_marker:
        sys.stderr.write("agy: error: %s\n" % args.stderr_marker)
    if args.exit_code != 0:
        return args.exit_code

    sys.stdout.write(args.emit if args.emit is not None else args.prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
