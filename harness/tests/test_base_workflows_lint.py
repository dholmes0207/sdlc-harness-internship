"""Mechanical lint for the data-driven base workflows.

The base workflows under `harness/plugins/hs/workflows/` run inside the
Workflow tool's locked VM. Three properties must hold and a static grep is the
cheap fast-fail for them (the real green-criterion is a smoke-run that does not
raise errorCode:4):

1. No determinism-trap tokens. The VM bans `Date.now()`, `Math.random()` and
   `new Date()` — touching any of them aborts the run with errorCode:4. Backoff
   must therefore be deterministic.
2. No VM-forbidden escapes: `import`, `require(`, `eval(`, `new Function`, and
   no Node fs access. Each base inlines its own helpers (the VM forbids sharing
   a module), so duplication of the backoff helper is expected and allowed.
3. Backoff is attempt-index based (`base * 2**attempt`), not wall-clock — proven
   by the presence of an attempt-indexed power expression and the absence of any
   banned clock token (already covered by #1).
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WF_DIR = REPO_ROOT / "harness" / "plugins" / "hs" / "workflows"

BASE_WORKFLOWS = [
    "base-pipeline-verify.js",
    "base-fanout-consolidate.js",
]

# (label, regex) — any match is a lint failure.
BANNED = [
    ("Date.now", re.compile(r"Date\.now")),
    ("Math.random", re.compile(r"Math\.random")),
    ("new Date", re.compile(r"new\s+Date")),
    ("import statement", re.compile(r"^\s*import\s", re.MULTILINE)),
    ("require(", re.compile(r"\brequire\s*\(")),
    ("eval(", re.compile(r"\beval\s*\(")),
    ("new Function", re.compile(r"new\s+Function")),
]

# attempt-indexed backoff: `2 ** attempt` or `Math.pow(2, attempt)`.
ATTEMPT_INDEX_BACKOFF = re.compile(r"Math\.pow\(\s*2\s*,\s*attempt\s*\)|2\s*\*\*\s*attempt")

_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/")
_LINE_COMMENT = re.compile(r"//[^\n]*")


def _read(name):
    path = WF_DIR / name
    assert path.is_file(), f"base workflow missing: {path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(src):
    """Scan CODE, not prose. The base files document the banned tokens in their
    header comments on purpose; the determinism trap only fires on tokens in
    EXECUTED code, so the lint strips comments before grepping. Safe for these
    files: no `//` appears inside any string or regex literal here (the only
    regex literal is `/\\{\\{(\\w+)\\}\\}/g`)."""
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", src))


@pytest.mark.parametrize("name", BASE_WORKFLOWS)
def test_base_workflow_exists(name):
    _read(name)


@pytest.mark.parametrize("name", BASE_WORKFLOWS)
def test_no_banned_tokens(name):
    src = _strip_comments(_read(name))
    hits = [label for label, rx in BANNED if rx.search(src)]
    assert not hits, f"{name} contains VM-banned token(s) in code: {hits}"


@pytest.mark.parametrize("name", BASE_WORKFLOWS)
def test_backoff_is_attempt_indexed(name):
    src = _read(name)
    assert ATTEMPT_INDEX_BACKOFF.search(src), (
        f"{name} must use attempt-indexed backoff (base * 2**attempt), "
        "not wall-clock jitter"
    )


# Control chars other than tab/newline/carriage-return. The Workflow approval
# dialog rejects any script carrying these ("control characters that would be
# hidden in the approval dialog"), so a base file that embeds one is unusable.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@pytest.mark.parametrize("name", BASE_WORKFLOWS)
def test_no_control_chars(name):
    src = _read(name)
    bad = sorted({hex(ord(c)) for c in _CONTROL_CHARS.findall(src)})
    assert not bad, (
        f"{name} contains control char(s) {bad} — the Workflow approval dialog "
        "rejects scripts with hidden control characters"
    )


@pytest.mark.parametrize("name", BASE_WORKFLOWS)
def test_meta_is_first_export(name):
    src = _read(name)
    assert re.search(r"export\s+const\s+meta\s*=", src), (
        f"{name} must declare `export const meta = {{...}}`"
    )


# --- structural anti-waste knobs (P4): group-cap + early-write ---------------
# Both bases accept a groupCap + earlyWrite arg and surface a VISIBLE warning
# (log()) when a run exceeds the cap or omits early-write. Structural + visible,
# never a throw (a throw would kill the run and the recall it protects).

@pytest.mark.parametrize("name", BASE_WORKFLOWS)
def test_base_accepts_group_cap_arg(name):
    src = _read(name)
    assert "spec.groupCap" in src, f"{name} must read spec.groupCap"
    assert "group-cap" in src, f"{name} must emit a group-cap warning label"
    # visible warn, not a throw: the cap branch calls log(), never throw.
    assert re.search(r"log\([^\n]*group-cap", src), (
        f"{name} must warn via log() when over the group cap"
    )


@pytest.mark.parametrize("name", BASE_WORKFLOWS)
def test_base_accepts_early_write_arg(name):
    src = _read(name)
    assert "spec.earlyWrite" in src, f"{name} must read spec.earlyWrite"
    assert "write_finding.py" in src, (
        f"{name} must bake the write_finding.py flush instruction into the lens prompt"
    )


@pytest.mark.parametrize("name", BASE_WORKFLOWS)
def test_early_write_warns_when_absent(name):
    src = _read(name)
    assert re.search(r"WARN early-write", src), (
        f"{name} must warn via log() when early-write is not configured"
    )


@pytest.mark.parametrize("name", BASE_WORKFLOWS)
def test_group_cap_branch_does_not_throw(name):
    # the cap handling must stay structural+visible: no `throw` introduced around it.
    src = _strip_comments(_read(name))
    assert "throw" not in src, (
        f"{name} must not throw — the cap/early-write signals are visible warnings, "
        "not run-killing exceptions"
    )


def test_pipeline_verify_wave_is_cap_guarded():
    # base-pipeline-verify's verify stage spawns one agent per finding; without a
    # guard a lens returning many findings blows the wave past the cap (the exact
    # one-sub-per-finding anti-pattern). It must surface a VISIBLE warn when a lens's
    # findings exceed the cap — structural+visible, same as the lens group-cap warn.
    src = _read("base-pipeline-verify.js")
    # a warn keyed to the verify wave (per-finding fan-out), referencing the cap
    assert re.search(r"log\([^\n]*verify[^\n]*cap", src, re.IGNORECASE), (
        "base-pipeline-verify.js must log() a warning when the per-lens verify "
        "wave (one agent per finding) exceeds the group cap"
    )
    # the guard must read the per-lens findings count against groupCap
    assert re.search(r"findings[^\n]*length[^\n]*groupCap|groupCap[^\n]*findings[^\n]*length", src), (
        "the verify-wave guard must compare the lens's findings count to groupCap"
    )
