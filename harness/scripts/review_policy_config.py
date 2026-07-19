#!/usr/bin/env python3
"""review_policy_config.py — read/set the shared review-policy.yaml.

review-policy.yaml names tactical review profiles (rounds + the four axes), a
per-hard-stage effort/rounds floor (ships OFF), and caps. This module is the
single reader/writer for it.

Fail-open non-breaking: an ABSENT file resolves to a default with every stage
floor disabled, so a fresh install behaves exactly as before. A PRESENT but
malformed file (non-mapping, bad enum) raises ReviewPolicyConfigError so the CLI
can point the human at the typo — but the gate that consults this policy wraps
the call in try/except and treats any raise as a no-op (the floor is a
self-discipline tier, never a real boundary).

The writer is SURGICAL and block-scoped: it rewrites ONLY the one nested line
named by a `stage_floor.<stage>.<knob>` or `profiles.<name>.<knob>` dot-key,
leaving comments and every other line byte-for-byte intact. Read path resolves
the tracked file off __file__ (never CWD); tests pass `path=` explicitly.
"""

import re
from pathlib import Path

_REVIEW_POLICY_DEFAULT = (
    Path(__file__).resolve().parent.parent / "data" / "review-policy.yaml")

_VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}
_VALID_SCOPE = {"diff", "project"}
_STAGES = ("pr", "merge", "ship", "deploy")

# knob -> coercion type for the surgical writer / loader validation.
_PROFILE_KNOBS = {
    "rounds": "int", "compounding": "bool", "per_aspect": "bool",
    "blind_main_sub": "bool", "refute": "bool", "effort": "effort",
    "scope": "scope",
}
_FLOOR_KNOBS = {"enabled": "bool", "min_effort": "effort", "min_rounds": "int"}


class ReviewPolicyConfigError(Exception):
    """Raised when review-policy.yaml is malformed or a write is rejected; the
    message names the file/key so the fix is a config edit, not a debug session."""


def _default_policy() -> dict:
    """The all-floors-OFF default returned when the file is absent. A fresh dict
    each call so callers can never mutate a shared default."""
    def _floor():
        return {"enabled": False, "min_effort": "low", "min_rounds": 1}
    return {
        "profiles": {
            "default": {"rounds": 1, "compounding": False, "per_aspect": False,
                        "blind_main_sub": False, "refute": False,
                        "effort": "low", "scope": "diff",
                        "aspects": ["correctness"]},
            "thorough": {"rounds": 3, "compounding": True, "per_aspect": True,
                         "blind_main_sub": False, "refute": True,
                         "effort": "high", "scope": "diff",
                         "aspects": ["security", "dry", "correctness",
                                     "consistency"]},
            "ship-grade": {"rounds": 3, "compounding": True, "per_aspect": True,
                           "blind_main_sub": True, "refute": True,
                           "effort": "max", "scope": "project",
                           "aspects": ["security", "dry", "correctness",
                                       "consistency"]},
        },
        "stage_floor": {s: _floor() for s in _STAGES},
        "caps": {"max_rounds": 5, "max_lenses_per_round": 8},
    }


def _validate_policy(policy: dict, source) -> None:
    """Reject out-of-enum effort/scope so a bad file fails loudly at load."""
    for name, prof in (policy.get("profiles") or {}).items():
        if not isinstance(prof, dict):
            continue
        eff = prof.get("effort")
        if eff is not None and eff not in _VALID_EFFORT:
            raise ReviewPolicyConfigError(
                "profile %r in %s: effort must be one of %s (got %r)"
                % (name, source, sorted(_VALID_EFFORT), eff))
        scope = prof.get("scope")
        if scope is not None and scope not in _VALID_SCOPE:
            raise ReviewPolicyConfigError(
                "profile %r in %s: scope must be one of %s (got %r)"
                % (name, source, sorted(_VALID_SCOPE), scope))
    for stage, floor in (policy.get("stage_floor") or {}).items():
        if not isinstance(floor, dict):
            continue
        me = floor.get("min_effort")
        if me is not None and me not in _VALID_EFFORT:
            raise ReviewPolicyConfigError(
                "stage_floor.%s in %s: min_effort must be one of %s (got %r)"
                % (stage, source, sorted(_VALID_EFFORT), me))


def load_review_policy(path=None) -> dict:
    """Parse review-policy.yaml. Missing file -> the all-floors-OFF default (no
    raise). A non-mapping document or an out-of-enum value raises
    ReviewPolicyConfigError. Present-but-partial documents are filled from the
    default per top-level block so resolve_profile always finds `default`."""
    import yaml  # lazy

    p = Path(path) if path else _REVIEW_POLICY_DEFAULT
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _default_policy()
    if raw is None:
        return _default_policy()
    if not isinstance(raw, dict):
        raise ReviewPolicyConfigError(
            "review policy %s is malformed — expected a YAML mapping" % p)
    _validate_policy(raw, p)
    merged = _default_policy()
    for block in ("profiles", "stage_floor", "caps"):
        if isinstance(raw.get(block), dict):
            merged[block].update(raw[block])
    return merged


def resolve_profile(name, policy) -> dict:
    """Return the named profile, falling back to `default` (never crashing) for
    an unknown name + a stderr warning."""
    import sys
    profiles = policy.get("profiles") or {}
    if name in profiles:
        return profiles[name]
    sys.stderr.write(
        "review-policy: unknown profile %r — falling back to `default`\n" % name)
    return profiles.get("default", _default_policy()["profiles"]["default"])


def _coerce(knob, kind, value):
    """Coerce a raw (CLI string or Python) value to the knob's type, validating
    enums. Raises ReviewPolicyConfigError on a bad value."""
    if kind == "bool":
        if isinstance(value, bool):
            return value
        low = str(value).strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ReviewPolicyConfigError("knob %r expects a boolean (got %r)"
                                      % (knob, value))
    if kind == "int":
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise ReviewPolicyConfigError("knob %r expects an integer (got %r)"
                                          % (knob, value))
    if kind == "effort":
        v = str(value).strip()
        if v not in _VALID_EFFORT:
            raise ReviewPolicyConfigError(
                "knob %r must be one of %s (got %r)"
                % (knob, sorted(_VALID_EFFORT), value))
        return v
    if kind == "scope":
        v = str(value).strip()
        if v not in _VALID_SCOPE:
            raise ReviewPolicyConfigError(
                "knob %r must be one of %s (got %r)"
                % (knob, sorted(_VALID_SCOPE), value))
        return v
    raise ReviewPolicyConfigError("unknown coercion kind %r" % kind)  # pragma: no cover


def _parse_dot_key(dot_key):
    """Resolve a `<block>.<sub>.<knob>` dot-key to (block, sub, knob, kind).
    Unknown shape/block/knob raises ReviewPolicyConfigError before any write."""
    parts = dot_key.split(".")
    if len(parts) != 3:
        raise ReviewPolicyConfigError(
            "unknown review-policy key %r — expected "
            "stage_floor.<stage>.<knob> or profiles.<name>.<knob>" % dot_key)
    block, sub, knob = parts
    if block == "stage_floor":
        if sub not in _STAGES:
            raise ReviewPolicyConfigError(
                "unknown stage %r — expected one of %s" % (sub, list(_STAGES)))
        if knob not in _FLOOR_KNOBS:
            raise ReviewPolicyConfigError(
                "unknown stage_floor knob %r — expected one of %s"
                % (knob, sorted(_FLOOR_KNOBS)))
        return block, sub, knob, _FLOOR_KNOBS[knob]
    if block == "profiles":
        if knob not in _PROFILE_KNOBS:
            raise ReviewPolicyConfigError(
                "unknown profile knob %r — expected one of %s"
                % (knob, sorted(_PROFILE_KNOBS)))
        return block, sub, knob, _PROFILE_KNOBS[knob]
    raise ReviewPolicyConfigError(
        "unknown review-policy block %r — expected stage_floor or profiles"
        % block)


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _rewrite_nested_line(lines, block, sub, knob, formatted):
    """Block-scoped surgical rewrite: find `^block:`, then the `sub:` line under
    it (by indent), then the `knob:` line in that sub-block, and rewrite only its
    value — preserving the key's leading whitespace. Returns True on success."""
    block_re = re.compile(r"^(\s*)%s\s*:\s*$" % re.escape(block))
    i = 0
    n = len(lines)
    # locate block
    while i < n and not block_re.match(lines[i]):
        i += 1
    if i >= n:
        return False
    block_indent = len(block_re.match(lines[i]).group(1))
    i += 1
    # locate sub-block under block
    sub_re = re.compile(r"^(\s+)%s\s*:\s*$" % re.escape(sub))
    sub_indent = None
    while i < n:
        line = lines[i]
        if line.strip() and not line.lstrip().startswith("#"):
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= block_indent:
                return False  # left the block without finding sub
        m = sub_re.match(line)
        if m and (len(m.group(1)) > block_indent):
            sub_indent = len(m.group(1))
            i += 1
            break
        i += 1
    if sub_indent is None:
        return False
    # locate knob line within sub-block
    knob_re = re.compile(r"^(\s+)%s\s*:\s*(.*)$" % re.escape(knob))
    while i < n:
        line = lines[i]
        if line.strip() and not line.lstrip().startswith("#"):
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent <= sub_indent:
                return False  # left the sub-block without finding knob
        m = knob_re.match(line)
        if m and len(m.group(1)) > sub_indent:
            lines[i] = "%s%s: %s\n" % (m.group(1), knob, formatted)
            return True
        i += 1
    return False


def save_review_policy(updates: dict, path=None) -> Path:
    """Surgically set one or more nested knobs. Every key/value is validated
    BEFORE any write; an unknown key or bad value raises ReviewPolicyConfigError
    and the file is left untouched. Comments and untouched lines are preserved."""
    p = Path(path) if path else _REVIEW_POLICY_DEFAULT
    if not updates:
        return p
    # validate everything first (no partial writes)
    resolved = []
    for dot_key, value in updates.items():
        block, sub, knob, kind = _parse_dot_key(dot_key)
        coerced = _coerce(knob, kind, value)
        resolved.append((block, sub, knob, _format_value(coerced)))

    text = p.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    for block, sub, knob, formatted in resolved:
        if not _rewrite_nested_line(lines, block, sub, knob, formatted):
            raise ReviewPolicyConfigError(
                "could not locate %s.%s.%s in %s — key path not present in file"
                % (block, sub, knob, p))
    from register_store import atomic_write
    atomic_write(p, "".join(lines))
    return p


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(
        description="read/set the shared review-policy.yaml (profiles + "
                    "stage_floor + caps)")
    ap.add_argument("--file", default=None,
                    help="explicit review-policy.yaml path (default: shipped file)")
    ap.add_argument("--set", dest="sets", action="append", metavar="KEY=VALUE",
                    help="surgically set stage_floor.<stage>.<knob> or "
                         "profiles.<name>.<knob>")
    args = ap.parse_args(argv)
    path = args.file
    if not args.sets:
        import json
        print(json.dumps(load_review_policy(path=path), indent=2,
                         ensure_ascii=False))
        return 0
    updates = {}
    for pair in args.sets:
        if "=" not in pair:
            sys.stderr.write("--set expects KEY=VALUE, got %r\n" % pair)
            return 2
        key, value = pair.split("=", 1)
        updates[key] = value.strip()
    try:
        out = save_review_policy(updates, path=path)
    except ReviewPolicyConfigError as e:
        sys.stderr.write("ReviewPolicyConfigError: %s\n" % e)
        return 1
    print("saved review-policy → %s" % out)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
