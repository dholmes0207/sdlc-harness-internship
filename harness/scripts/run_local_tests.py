#!/usr/bin/env python3
"""run_local_tests.py — run the SAME pytest command CI runs, from one subcommand.

Reading `scripts/ci.sh` prose and hand-crafting the equivalent pytest invocation drifts
over time (a scheduler/marker/flag changes on one side and not the other). This wrapper
reads `harness/data/test-strategy.yaml` (the SSOT profile table, kept in lockstep with
ci.sh by `test_run_local_tests.py::test_strategy_matches_ci_sh`) and builds the argv —
so a dev or agent runs `--harness` / `--orchestrator` / etc. instead of remembering flags.

`build_argv` is pure (no subprocess) so it is unit-tested directly; `main` is the only
seam that shells out, and `--all` always runs its profiles SEQUENTIALLY — a personal
16-core box does not want two xdist fan-outs contending for the same cores at once.
"""
import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_DEFAULT_STRATEGY_PATH = Path(__file__).resolve().parent.parent / "data" / "test-strategy.yaml"

# --all runs the 3 full suites sequentially (harness/orchestrator/release) — the
# dev-loop-only `harness-fast` subset and the sub-second `scoring` check are each a
# single flag away and are not folded into --all.
_ALL_PROFILES = ("harness", "orchestrator", "release")

# argparse dest (dashes -> underscores) -> profile key in test-strategy.yaml.
_FLAG_TO_PROFILE = {
    "harness": "harness",
    "harness_fast": "harness-fast",
    "orchestrator": "orchestrator",
    "release": "release",
    "scoring": "scoring",
}


def load_strategy(path) -> Dict[str, Dict[str, Any]]:
    """Parse + validate test-strategy.yaml's `profiles` table. Raises ValueError on a
    missing/empty table or a profile entry missing its required `path` key — a caller
    should not get a confusing KeyError three calls deep in build_argv."""
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"{path}: missing or empty 'profiles' table")
    for name, cfg in profiles.items():
        if not isinstance(cfg, dict) or "path" not in cfg:
            raise ValueError(f"{path}: profile '{name}' missing required 'path' key")
    return profiles


def build_argv(profile: str, strategy: Dict[str, Dict[str, Any]]) -> List[str]:
    """Pure argv builder — path, -q, xdist scheduler (unless xdist: false), marker,
    -p no:randomly, per-ignore flags. No subprocess call here; that lives in main()."""
    if profile not in strategy:
        raise ValueError(f"unknown profile '{profile}' — known: {sorted(strategy)}")
    cfg = strategy[profile]
    path = cfg["path"]
    argv: List[str] = ["python3", "-m", "pytest", path, "-q"]

    if cfg.get("xdist", True) is not False:
        scheduler = cfg.get("scheduler", "load")
        argv += ["-n", "auto", "--dist", scheduler]

    marker = cfg.get("marker")
    if marker:
        argv += ["-m", marker]

    if cfg.get("norandomly"):
        argv += ["-p", "no:randomly"]

    for name in cfg.get("ignore") or []:
        argv.append(f"--ignore={path}/{name}")

    return argv


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--harness", action="store_true", help="full harness/tests, loadfile")
    group.add_argument("--harness-fast", action="store_true", help="harness subset, skips the heaviest files")
    group.add_argument("--orchestrator", action="store_true", help="orchestrator/tests, loadfile")
    group.add_argument("--release", action="store_true", help="release/tests, --dist load")
    group.add_argument("--scoring", action="store_true", help="orchestrator/tests -m scoring_contract")
    group.add_argument("--all", action="store_true", help="harness + orchestrator + release, sequentially")
    parser.add_argument("--strategy", default=str(_DEFAULT_STRATEGY_PATH), help="path to test-strategy.yaml")
    parser.add_argument(
        "--collect-only", "--dry",
        dest="dry", action="store_true",
        help="pass --collect-only through to pytest so scope/count can be confirmed without running tests",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    strategy = load_strategy(args.strategy)

    if args.all:
        profiles = list(_ALL_PROFILES)
    else:
        profiles = [profile for flag, profile in _FLAG_TO_PROFILE.items() if getattr(args, flag)]

    exit_code = 0
    for profile in profiles:
        cmd = build_argv(profile, strategy)
        if args.dry:
            cmd = cmd + ["--collect-only"]
        print(f"$ {' '.join(cmd)}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            exit_code = result.returncode
            break
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
