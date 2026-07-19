#!/usr/bin/env python3
"""validate_artifacts.py — a WARN-class checker that holds an artifact against
one of the JSON schemas in harness/schemas/.

The schemas there are documentation-grade: they describe the full shape of each
machine-written artifact, but until now nothing checked a real file against
them — the live presence gate (artifact_check.py) only does a flat
required-fields scan. This script closes that gap WITHOUT pulling in jsonschema:
it hand-rolls the small slice of draft-2020-12 the harness schemas actually use
(type / required / enum / const, plus object-properties and array-items
recursion).

It is advisory by design: validate() never raises and the CLI always exits 0,
emitting findings as JSON. A mismatch is a warning to a human, never a block —
the authoritative gate stays artifact_check.py.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# JSON Schema "type" name -> the Python type(s) that satisfy it. bool is split
# out of int explicitly: in Python `True` is an int, but a schema asking for an
# integer must not silently accept a boolean.
_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def _matches_type(value: Any, type_name: str) -> bool:
    """Does `value` satisfy the JSON-schema primitive `type_name`?"""
    py = _JSON_TYPES.get(type_name)
    if py is None:
        # Unknown type keyword: don't flag — this checker only knows the slice
        # the harness schemas use, and an unrecognized keyword is not a value
        # error.
        return True
    if isinstance(value, bool):
        # A boolean only satisfies "boolean" (never integer/number) — guard
        # before the numeric checks below, since bool subclasses int.
        return type_name == "boolean"
    return isinstance(value, py)


def _type_ok(value: Any, type_spec: Any) -> bool:
    """`type` may be a single name or a list of names (a union, e.g.
    ["integer", "null"]). The value passes if it matches ANY listed type."""
    if isinstance(type_spec, list):
        return any(_matches_type(value, t) for t in type_spec)
    if isinstance(type_spec, str):
        return _matches_type(value, type_spec)
    return True  # no/odd type keyword -> nothing to assert


def _check_value(value: Any, subschema: Dict[str, Any],
                 path: str, findings: List[str]) -> None:
    """Validate one value against one subschema, appending WARN strings to
    `findings`. Recurses into object properties and array items. Tolerant:
    a non-dict subschema, or any shape it does not understand, simply adds no
    finding rather than raising."""
    if not isinstance(subschema, dict):
        return

    if "type" in subschema and not _type_ok(value, subschema["type"]):
        findings.append(
            "%s: expected type %r, got %s"
            % (path, subschema["type"], type(value).__name__))
        # Type is wrong; deeper checks (enum/items/properties) would be noise.
        return

    if "const" in subschema and value != subschema["const"]:
        findings.append(
            "%s: expected const %r, got %r" % (path, subschema["const"], value))

    if "enum" in subschema and isinstance(subschema["enum"], list):
        if value not in subschema["enum"]:
            findings.append(
                "%s: value %r not in enum %r" % (path, value, subschema["enum"]))

    # Recurse into a nested object's declared properties + required list.
    if isinstance(value, dict):
        _check_object(value, subschema, path, findings)

    # Recurse into array items (a single items-subschema applied to each).
    if isinstance(value, list):
        items = subschema.get("items")
        if isinstance(items, dict):
            for i, elem in enumerate(value):
                _check_value(elem, items, "%s[%d]" % (path, i), findings)


def _check_object(obj: Dict[str, Any], schema: Dict[str, Any],
                  path: str, findings: List[str]) -> None:
    """required + per-property recursion for an object node."""
    for field in schema.get("required", []) or []:
        if field not in obj:
            here = "%s.%s" % (path, field) if path else field
            findings.append("%s: required field missing" % here)

    props = schema.get("properties")
    if isinstance(props, dict):
        for name, subschema in props.items():
            if name in obj:
                child = "%s.%s" % (path, name) if path else name
                _check_value(obj[name], subschema, child, findings)


def validate(schema: Any, artifact: Any) -> List[str]:
    """Hold `artifact` against `schema`, returning a list of WARN strings (one
    per mismatch); an empty list means clean. NEVER raises — a non-dict schema
    or artifact yields findings (or none), so a caller can treat any input
    safely.

    Covered keywords: type (incl. unions), required, enum, const, nested
    object `properties`, array `items`. Anything else is ignored (this is a
    documentation-grade checker, not a full validator)."""
    findings: List[str] = []
    try:
        if not isinstance(schema, dict):
            return findings  # nothing to assert against
        if not isinstance(artifact, dict):
            # The harness schemas are all top-level objects; a non-object
            # artifact is itself the mismatch.
            findings.append(
                "<root>: expected an object, got %s" % type(artifact).__name__)
            return findings
        _check_value(artifact, schema, "", findings)
    except Exception as exc:  # noqa: BLE001 — WARN-class must never raise
        findings.append("<validator>: skipped a check (%s)" % exc)
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(
        description="WARN-class artifact validator (never blocks).")
    ap.add_argument("--schema", required=True,
                    help="path to a JSON schema in harness/schemas/")
    ap.add_argument("--artifact", required=True,
                    help="path to the JSON artifact to check")
    args = ap.parse_args()

    out: Dict[str, Any] = {"schema": args.schema, "artifact": args.artifact,
                           "findings": []}
    try:
        schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["findings"] = ["<schema>: could not load (%s)" % exc]
        print(json.dumps(out, ensure_ascii=False))
        return 0
    try:
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["findings"] = ["<artifact>: could not load (%s)" % exc]
        print(json.dumps(out, ensure_ascii=False))
        return 0

    out["findings"] = validate(schema, artifact)
    print(json.dumps(out, ensure_ascii=False))
    return 0  # WARN-class: advisory only, never blocks


if __name__ == "__main__":
    sys.exit(main())
