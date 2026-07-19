#!/usr/bin/env python3
"""secret_scan_before_ship.py — pre-ship secret-leak gate (compliance, fail-closed).

Fires only at the leak boundary — a Bash command that advances the push/pr/ship/deploy
stage (stage_detector) — and scans the diff of the commits about to LEAVE the machine
(`git log --branches --not --remotes -p`). A likely secret in an added line of a
non-excluded file blocks the op with an actionable reason. test/fixture/docs paths are
excluded so the gate never self-blocks on its own fixtures and avoids the common false
positive. The thorough backstop is the hs:security-scan skill; this gate catches
the high-confidence machine-readable leak at the last moment.

Posture: compliance, fail-CLOSED on its own errors (run_compliance_hook). A git failure
that yields no diff is treated as nothing-to-scan (the wrapper passes on absent signal,
not on a detected secret). Break-glass: enabled:false in harness-hooks.yaml.
"""
import os
import re
import subprocess
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_HOOKS_DIR, os.path.join(_HOOKS_DIR, "..", "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hook_runtime  # noqa: E402

HOOK_CLASS = "compliance"
_HOOK = "secret_scan_before_ship"

# Stages where committed content actually leaves the machine.
_SHIP_STAGES = ("push", "pr", "ship", "deploy")

# High-confidence machine-readable secret shapes (mirrors the security-scan skill's
# secret-and-dependency reference). Precision-first: each requires a distinctive prefix
# or a key=quoted-value assignment, so prose does not false-match.
_PATTERNS = [
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws-secret-key", re.compile(
        r"(?i)aws_?secret_?access_?key\s*[:=]\s*['\"]?[A-Za-z0-9/+]{40}\b")),
    ("pem-private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("pgp-private-key", re.compile(r"-----BEGIN[ ]PGP[ ]PRIVATE[ ]KEY[ ]BLOCK-----")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github-fine-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("gitlab-pat", re.compile(r"\bglpat-[A-Za-z0-9_-]{20}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b")),
    ("twilio-key", re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
    ("hf-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("do-token", re.compile(r"\bdop_v1_[a-f0-9]{64}\b")),
    ("azure-account-key", re.compile(r"AccountKey=[A-Za-z0-9/+]{40,}={0,2}")),
    ("db-uri-cred", re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:/@]+:[^\s:/@]+@([^\s:/?#]+)")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("generic-secret", re.compile(
        r"""(?i)(?:api[_-]?key|apikey|api[_-]?secret|secret|token|credential|password|passwd|pwd)\s*[:=]\s*['"][A-Za-z0-9/+=_-]{16,}['"]""")),
    ("generic-secret-unquoted", re.compile(
        r"""(?i)(?:api[_-]?key|apikey|api[_-]?secret|secret|token|credential|password|passwd|pwd)\s*[:=]\s*['"]?(?=[A-Za-z0-9/+=_-]*[0-9])[A-Za-z0-9/+=_-]{16,}""")),
]

# Paths excluded from scanning: tests, fixtures, examples, docs, lockfiles. Excluding
# them is standard secret-scan practice and prevents the gate from blocking on its own
# fake-secret fixtures.
_EXCLUDE_RE = re.compile(
    r"(?:^|/)(?:tests?|spec|specs|fixtures?|examples?|samples?|mocks?|__tests__)(?:/|$)"
    # test-FILE names are excluded only for the .py (pytest) convention — a
    # `test_*.py` at any depth is conventionally a test holding FAKE secrets. A
    # file merely NAMED test_/_test in another language (lib/test_helpers.js, Go
    # src/server_test.go) is a real published module, so scan it.
    r"|(?:^|/)test_[^/]*\.py$|(?:^|/)[^/]*_test\.py$"
    r"|\.(?:md|lock|example|sample|template|dist)$",
    re.IGNORECASE)


def _excluded(path: str) -> bool:
    return bool(_EXCLUDE_RE.search(path or ""))


# A db connection string whose HOST is local-dev is not a remotely-usable leak: the
# scheme://user:pass@host form only exposes a credential an attacker can actually USE
# when the host resolves OFF the machine. localhost / loopback / a dotless docker-
# compose service name / *.local resolve only inside the dev box or its compose
# network, so postgres://postgres:postgres@localhost or mongodb://admin:admin@mongo is
# skipped (the common docker-compose false positive). A dotted FQDN (db.internal,
# cluster0.x.mongodb.net) or a non-loopback IP still fires — that is a real leak.
_DB_LOCAL_HOST_RE = re.compile(
    r"(?i)^(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[?::1\]?"
    r"|[a-z0-9_-]+"                       # single-label service name (no dot -> compose/dev)
    r"|[a-z0-9_-]+\.(?:local|localhost))$")


def _db_host_is_local(host: str) -> bool:
    return bool(_DB_LOCAL_HOST_RE.match((host or "").strip()))


# Documentation placeholders the generic key=value patterns must NOT flag (a secret
# gate that false-blocks `API_KEY=\'your-api-key-here\'` help text gets disabled). The
# high-confidence prefix patterns never match these, so the exemption is generic-only.
_PLACEHOLDER_RE = re.compile(
    r"(?i)\b(?:your|example|sample|placeholder|changeme|dummy|redacted|todo)"
    r"|<[A-Za-z_]|\.\.\.|here['\"]?\s*$")


def scan_text(text: str) -> list:
    """Return the names of secret patterns that match `text` (deduped, ordered). The
    generic key=value patterns skip an obvious documentation placeholder value; the
    prefix/var-anchored patterns are high-confidence and always count."""
    text = text or ""
    hits = []
    for name, rx in _PATTERNS:
        for m in rx.finditer(text):
            if name.startswith("generic-secret") and _PLACEHOLDER_RE.search(m.group(0)):
                continue  # a real secret elsewhere still fires; a lone placeholder does not
            if name == "db-uri-cred" and _db_host_is_local(m.group(1)):
                continue  # local-dev host — not remotely usable; a remote URI still fires
            if name not in hits:
                hits.append(name)
            break
    return hits


def scannable_added_lines(diff: str) -> str:
    """The added (`+`) content of non-excluded files in a unified diff, header-stripped."""
    out = []
    excluded = False
    prev_minus_header = False
    for line in (diff or "").splitlines():
        # A "+++ b/path" file header is ONLY a header when it directly follows
        # a "--- a/path" line (the unified-diff pair). A bare "+++ ..." with no
        # preceding "--- " is ADDED CONTENT whose own text starts with "++"
        # ("+" added-marker + "++..."); treating it as a header would drop that
        # content and let a secret on such a line evade the scan.
        if line.startswith("--- "):
            prev_minus_header = True
            continue
        if prev_minus_header and line.startswith("+++ "):
            raw = line[4:].strip()
            path = raw[2:] if raw.startswith("b/") else raw
            excluded = _excluded(path)
            prev_minus_header = False
            continue
        prev_minus_header = False
        if excluded:
            continue
        if line.startswith("+"):
            out.append(line[1:])
    return "\n".join(out)


def gather_unpushed_diff(root: str) -> str:
    """The patch of commits not yet on any remote (everything, in a remote-less repo).
    Returns the diff string on success (possibly "" when nothing is unpushed), or
    None on a git error/timeout so the caller can FAIL CLOSED — an unverifiable diff
    for a secret gate must block, not silently pass. No --max-count cap: a secret in
    an older unpushed commit must be scanned; a scan too slow to finish times out and
    fails closed rather than skipping it."""
    try:
        r = subprocess.run(
            ["git", "-C", root, "log", "--branches", "--not", "--remotes",
             "-p", "--no-color"],
            capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def scan_diff_text(diff: str):
    """The detection core shared by the in-session gate (gate_reason) and the
    transport backstop (push_gate._secret_reason): scan a unified diff's added
    lines for secrets, then re-scan with added-line breaks removed, to catch a
    secret wrapped across two added lines (e.g. an AKIA id split mid-token).
    Over-detection is fine for a secret gate; the distinctive prefixes keep
    cross-line false-matches rare. Returns the matched pattern names (possibly
    empty)."""
    added = scannable_added_lines(diff)
    hits = scan_text(added)
    if not hits:
        hits = scan_text(added.replace("\n", ""))
    return hits


def gather_pack_surface(root: str):
    """The working-tree content a non-git packager (npm/cargo/docker/...) would
    transmit that is NOT in committed git history: uncommitted changes to tracked
    files PLUS the content of untracked, non-ignored files. Returns the scannable
    text, or None on a git error (caller fails CLOSED). For ship/deploy stages, the
    packed artifact — not unpushed commits — is what leaves the machine."""
    try:
        d = subprocess.run(["git", "-C", root, "diff", "HEAD", "--no-color"],
                           capture_output=True, text=True, timeout=30)
        if d.returncode != 0:
            return None
        parts = [scannable_added_lines(d.stdout)]
        o = subprocess.run(["git", "-C", root, "ls-files", "--others", "--exclude-standard"],
                           capture_output=True, text=True, timeout=30)
        if o.returncode != 0:
            return None
        for rel in o.stdout.splitlines():
            rel = rel.strip()
            if not rel or _excluded(rel):
                continue
            try:
                with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                    chunk = fh.read(1_000_000)  # cap: a secret sits near the top of a
                    #                             config file; bound memory/time so a
                    #                             large untracked artifact can't hang the gate
            except OSError:
                continue
            if "\x00" in chunk[:4096]:
                continue  # binary (keystore/image/archive) — no text secret to scan
            parts.append(chunk)
        return "\n".join(parts)
    except Exception:
        return None


def gate_reason(command: str, root: str):
    """None ⇒ allow; string ⇒ block reason. Only ship-class stages are scanned."""
    import stage_detector
    stage = stage_detector.detect_stage(command)
    if stage not in _SHIP_STAGES:
        return None
    diff = gather_unpushed_diff(root)
    if diff is None:
        return ("secret-scan could not read the unpushed diff for %s (git error or "
                "timeout) — failing closed. Verify no secrets and push manually, or "
                "break-glass via enabled:false in harness/data/harness-hooks.yaml." % stage)
    hits = scan_diff_text(diff)
    if stage in ("ship", "deploy"):
        # ship/deploy have NO git transport backstop, and the packager transmits the
        # working tree / untracked pack surface, not unpushed commits — so scan that too.
        pack = gather_pack_surface(root)
        if pack is None:
            return ("secret-scan could not read the working-tree pack surface for %s "
                    "(git error or timeout) — failing closed. Verify no secrets, or "
                    "break-glass via enabled:false in harness/data/harness-hooks.yaml." % stage)
        if not hits:
            # pack is already scannable text (diff +lines + raw untracked content),
            # not a unified diff -> scan it directly, with the cross-line collapse pass.
            hits = scan_text(pack) or scan_text(pack.replace("\n", ""))
    if not hits:
        return None
    return ("secret-scan blocked %s: the diff to be pushed contains likely secrets (%s). "
            "Remove them from the commits (and rotate the credential if it is real) before "
            "shipping; run /hs:security-scan for a full audit, or break-glass via "
            "enabled:false in harness/data/harness-hooks.yaml." % (stage, ", ".join(hits)))


def core(data):
    command = hook_runtime.bash_command(data)
    if not command:
        return None
    import harness_paths
    return gate_reason(command, str(harness_paths.project_root()))


def main() -> None:
    import json
    raw = hook_runtime.read_stdin_json()
    hook_runtime.run_compliance_hook(_HOOK, core, raw=json.dumps(raw))


if __name__ == "__main__":
    main()
