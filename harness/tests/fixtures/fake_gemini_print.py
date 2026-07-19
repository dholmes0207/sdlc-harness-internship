#!/usr/bin/env python3
"""fake_gemini_print — stand-in for `gemini -p … -o json`, wired via
HARNESS_GEMINI_PRINT_CMD.

Mirrors the shape probed live against gemini 0.49.0: `-o json` emits
{session_id, response, stats.models.<model>.tokens{...}} on stdout, exits, and
does NOT stay resident. The prompt is the VALUE of -p/--prompt; --approval-mode,
--skip-trust, -m/--model, --session-id, --resume are tolerated. Control knobs ride
in HARNESS_GEMINI_PRINT_CMD (the real gemini has no such flags): force an exit code
+ stderr marker (transient classification), a sleep (timeout), or malformed stdout.
Session echo: --resume/--session-id round-trips the id; otherwise a fresh uuid4.
"""
import argparse
import json
import sys
import time
import uuid


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("-p", "--prompt", dest="prompt", default="")
    ap.add_argument("--approval-mode", dest="approval_mode", default="default")
    ap.add_argument("-o", "--output-format", dest="output_format", default="text")
    ap.add_argument("-m", "--model", default="gemini-3.1-pro")
    ap.add_argument("--session-id", dest="session_id", default=None)
    ap.add_argument("-r", "--resume", dest="resume", default=None)
    ap.add_argument("--skip-trust", action="store_true")
    ap.add_argument("-y", "--yolo", action="store_true")
    # control knobs (fake-only)
    ap.add_argument("--exit-code", type=int, default=0)
    ap.add_argument("--stderr-marker", default=None)
    ap.add_argument("--emit-response", default=None,
                    help="response text to emit instead of echoing the prompt")
    ap.add_argument("--empty-response", action="store_true",
                    help="emit an empty response (exercise the canary empty-drift path)")
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--bad-json", action="store_true",
                    help="emit non-JSON stdout to exercise the parse-fail path")
    ap.add_argument("--nonobject-json", default=None,
                    help="emit VALID json that is not an object (e.g. 'null', '42', "
                         "'[1,2]') to exercise the non-dict payload path")
    args, _unknown = ap.parse_known_args(argv)

    if args.sleep:
        time.sleep(args.sleep)
    if args.stderr_marker:
        sys.stderr.write("gemini: error: %s\n" % args.stderr_marker)
    if args.exit_code != 0:
        return args.exit_code
    if args.bad_json:
        sys.stdout.write("not json at all")
        return 0
    if args.nonobject_json is not None:
        sys.stdout.write(args.nonobject_json)  # valid json, not a dict
        return 0

    sid = args.resume or args.session_id or str(uuid.uuid4())
    if args.empty_response:
        response = ""
    elif args.emit_response is not None:
        response = args.emit_response
    else:
        response = args.prompt
    payload = {
        "session_id": sid,
        "response": response,
        "stats": {
            "models": {
                args.model: {
                    "api": {"totalRequests": 1, "totalErrors": 0},
                    "tokens": {"input": 16040, "prompt": 16040, "candidates": 1,
                               "total": 16135, "cached": 0, "thoughts": 94, "tool": 0},
                }
            },
            "tools": {"totalCalls": 0},
            "files": {"totalLinesAdded": 0, "totalLinesRemoved": 0},
        },
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
