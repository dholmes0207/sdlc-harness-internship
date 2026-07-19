#!/usr/bin/env python3
"""preflight_deps.py — check that external deps the harness needs are importable.

Policy: external deps are allowed but CONTROLLED — this script is the
single registry. Run it once per machine after clone (and in CI before tests).
Missing deps: prints the exact install command and exits non-zero.

Posture downstream when someone skips this step:
  - compliance hooks fail CLOSED (exit 2 + the same install command),
  - telemetry/nudge hooks skip silently.

Usage:
    python3 harness/scripts/preflight_deps.py            # human report
    python3 harness/scripts/preflight_deps.py --quiet    # exit code only (hooks/CI)
"""

import importlib
import sys

# module-to-import -> pip distribution name
REQUIRED = {
    "yaml": "pyyaml",   # human-edited config files (harness-hooks/stage-policy/ownership)
    "pytest": "pytest", # official test runner
    # XML test results (JUnit/Cobertura) are untrusted in a CI pull-request
    # context; stdlib ElementTree is NOT safe against entity-expansion/XXE, so
    # the result readers parse through defusedxml. This is the documented
    # exception to "stdlib is enough" — stdlib is NOT enough for untrusted XML.
    "defusedxml": "defusedxml",
    # orchestrator (tầng-2) DRIVE-TEST measures line coverage at the merge-base via the
    # coverage CLI (`coverage run -m pytest` + `coverage json`). The orchestrator CI job
    # runs that leg on the runner too (test_drive_test coverage cases), so it is REQUIRED
    # everywhere — a missing coverage CLI surfaces there as `coverage-unmeasurable`.
    "coverage": "coverage",
}

# Optional build-time deps — missing is a warning, not failure.
# Only needed for dev tools (build_pack regenerate, etc.); runtime unaffected.
OPTIONAL = {
    "cairosvg": "cairosvg",  # SVG→PNG rasterize for build_pack.py (dev regen tool)
    # Parallel test runner. Missing => the suite still runs, just serially; ci_local.sh
    # falls back to serial when it's absent. CI installs it explicitly. NOT required
    # (a slower test run is not a compliance failure), so a warning here, never exit 1.
    "xdist": "pytest-xdist",
    # dead-name linter for the eval-bootstrap template invariant (renders each stamped
    # .py and asserts no unused import/local). CI installs it so the guard fires at the
    # merge gate; a bare local box without it skips that one test (importorskip), never fails.
    "pyflakes": "pyflakes",
    # Local reproducibility of the hot-path "-p no:randomly" win + makes the
    # randomly×split hazard visible: without it locally, a dev can't reproduce
    # what the nightly order-dependence job exercises. Not required on the hot
    # path (ci.sh conditionally disables it when present); the nightly workflow
    # installs it explicitly for itself.
    "pytest_randomly": "pytest-randomly",
    # Shards the unit suite into weighted cells on the remote matrix (--splits/--group,
    # see scripts/ci.sh unit-<i>of3 cases). Missing it locally just means the shard
    # cases aren't runnable by hand; CI installs it explicitly for the matrix.
    "pytest_split": "pytest-split",
    # JSON-schema validator for the shipped schema-lint gate tests (write-deny-policy,
    # spec-graph, dec-ledger, shape-*). Not a runtime dep — artifact_check deliberately
    # avoids it. A bare local box without it importorskips those tests; CI installs it
    # explicitly so the schema-drift gate actually fires at the merge point.
    "jsonschema": "jsonschema",
}


def missing_required() -> list:
    """Return pip names of REQUIRED deps that cannot be imported."""
    out = []
    for module, pip_name in REQUIRED.items():
        try:
            importlib.import_module(module)
        except ImportError:
            out.append(pip_name)
    return out


def missing_optional() -> list:
    """Return pip names of OPTIONAL deps that cannot be imported.
    Does NOT fail — caller decides whether to warn.
    """
    out = []
    for module, pip_name in OPTIONAL.items():
        try:
            importlib.import_module(module)
        except ImportError:
            out.append(pip_name)
    return out


def install_command(missing: list) -> str:
    return "pip install " + " ".join(sorted(missing))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    quiet = "--quiet" in argv
    missing = missing_required()
    if not missing:
        if not quiet:
            print("preflight OK: " + ", ".join(sorted(REQUIRED.values())))
            opt = missing_optional()
            if opt:
                sys.stderr.write(
                    "optional deps missing: %s — %s\n" % (
                        ", ".join(opt), install_command(opt)))
        return 0
    if not quiet:
        sys.stderr.write(
            "preflight FAILED — missing dependencies: %s\n"
            "Install with:\n    %s\n" % (", ".join(missing), install_command(missing))
        )
    return 1


# Backward compatibility: install.py and hooks call missing_deps().
missing_deps = missing_required


if __name__ == "__main__":
    sys.exit(main())
