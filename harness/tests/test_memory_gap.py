"""Tests for the deterministic memory-gap detector (`memory_gap.py`), tier 1.

`memory_gap` is the SCRIPT-only, single DRY home for "memory that looks
unrecorded". It emits structured signals (never a judgment), ALWAYS exits 0
(advisory), and is deterministic (same disk state → same JSON).

At THIS tier it carries exactly two signal types — the rest of the source
funnel (validate_no_marker / approved_changed_no_dec / judged_not_stored) is
deferred and intentionally NOT wired here:
  - `fence_breach` — a change landed outside the declared ownership zones
                     (reuses `check_fence.scan` — no copied porcelain logic).
  - `parse_error`  — an artifact the graph could not parse (advisory surface,
                     never a crash).

The presence-closure tests below are the anti-silent-no-op net: importing the
detector pulls the whole tier-1 module chain (check_fence → spec_graph →
frontmatter_parser → encoding_utils), so deleting ANY of them turns these RED
instead of letting the detector look alive while never firing. The registered
signal set is asserted as DATA so trimming a signal silently also trips them.
"""

import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_fence  # noqa: E402
import memory_gap  # noqa: E402

from conftest import make_proj, append_to, _git  # noqa: E402,F401

_proj = make_proj
_append_to = append_to


def _types(signals):
    return {s["type"] for s in signals}


def _by_type(signals, t):
    return [s for s in signals if s["type"] == t]


# ---------------------------------------------------------------------------
# presence closure (tier-0a): the registered set IS the tier-1 contract
# ---------------------------------------------------------------------------

def test_registered_signal_set_is_tier_one_contract():
    """The detector declares exactly the tier-1 signal types. This list is the
    spec of tier 1; tier 2 widens it. Asserting it as data means a silent trim
    (drop a signal without breaking an import) still fails here."""
    assert set(memory_gap.REGISTERED_SIGNAL_TYPES) == {"fence_breach", "parse_error"}


def test_collect_only_emits_registered_types(tmp_path):
    """Drive BOTH signals at once (an out-of-zone file + a malformed artifact) and
    assert every emitted type is one the detector registered — collect() can never
    emit an unregistered type, and both tier-1 types are reachable."""
    proj = _proj(tmp_path)
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
    bad = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    bad.write_text("---\n: : : not: valid: yaml: [\n---\n# broken\n", encoding="utf-8")

    signals = memory_gap.collect(proj)
    types = _types(signals)
    assert types <= set(memory_gap.REGISTERED_SIGNAL_TYPES), types
    assert "fence_breach" in types
    assert "parse_error" in types


# ---------------------------------------------------------------------------
# 1. clean spec → no signals
# ---------------------------------------------------------------------------

def test_no_signals_clean_spec(tmp_path):
    """A committed spec with nothing touched outside the fence and no parse error
    emits no signals (tier 1 carries no baseline/marker signals)."""
    proj = _proj(tmp_path)  # make_proj commits the baseline → clean working tree
    signals = memory_gap.collect(proj)
    assert signals == [], signals


# ---------------------------------------------------------------------------
# 2. fence_breach (reuses check_fence)
# ---------------------------------------------------------------------------

def test_fence_breach_detected(tmp_path):
    """A file written OUTSIDE the declared zones surfaces a `fence_breach` signal
    whose subject matches what `check_fence.scan` reports."""
    proj = _proj(tmp_path)
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")

    signals = memory_gap.collect(proj)
    breaches = _by_type(signals, "fence_breach")
    assert breaches, signals
    subjects = {s["subject"] for s in breaches}
    assert "src/app.py" in subjects
    # Subject set matches check_fence's own findings (no logic drift).
    fence_files = {f["file"] for f in check_fence.scan(proj)}
    assert subjects == fence_files


# ---------------------------------------------------------------------------
# 3. reuses check_fence (single home, no logic drift)
# ---------------------------------------------------------------------------

def test_reuses_check_fence(tmp_path):
    """The `fence_breach` subjects are EXACTLY the files `check_fence.scan` reports
    — proving the detector imports the fence logic rather than re-walking porcelain."""
    proj = _proj(tmp_path)
    (proj / "config").mkdir(parents=True, exist_ok=True)
    (proj / "config" / "x.yaml").write_text("k: v\n", encoding="utf-8")
    (proj / "notes.txt").write_text("hi\n", encoding="utf-8")

    fence_files = {f["file"] for f in check_fence.scan(proj)}
    gap_files = {s["subject"] for s in _by_type(memory_gap.collect(proj), "fence_breach")}
    assert gap_files == fence_files
    assert "config/x.yaml" in gap_files
    assert "notes.txt" in gap_files


# ---------------------------------------------------------------------------
# 3b. include_parse_errors gate — skip the heavy spec-graph parse when the
#     caller knows this turn cannot have introduced a docs/product parse error
# ---------------------------------------------------------------------------

def test_include_parse_errors_true_is_default(tmp_path):
    """Default collect() still surfaces a malformed artifact as a parse_error —
    the CLI contract and every existing caller are unchanged."""
    proj = _proj(tmp_path)
    bad = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    bad.write_text("---\n: : : not: valid: yaml: [\n---\n# broken\n", encoding="utf-8")
    assert "parse_error" in _types(memory_gap.collect(proj))


def test_include_parse_errors_false_skips_build_graph(tmp_path, monkeypatch):
    """include_parse_errors=False drops the parse_error pass AND never calls
    build_graph (the 220ms spec-tree parse) — only the cheap fence scan runs.
    A malformed artifact is therefore NOT surfaced, and build_graph is untouched."""
    proj = _proj(tmp_path)
    bad = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    bad.write_text("---\n: : : not: valid: yaml: [\n---\n# broken\n", encoding="utf-8")
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")

    calls = {"n": 0}
    real = memory_gap.build_graph

    def _spy(root):
        calls["n"] += 1
        return real(root)

    monkeypatch.setattr(memory_gap, "build_graph", _spy)
    signals = memory_gap.collect(proj, include_parse_errors=False)
    assert calls["n"] == 0, "build_graph must not run when parse errors are excluded"
    assert "parse_error" not in _types(signals)
    # the cheap fence signal still fires — scoping never suppresses the real gap
    assert "fence_breach" in _types(signals)


# ---------------------------------------------------------------------------
# 4. deterministic
# ---------------------------------------------------------------------------

def test_deterministic(tmp_path):
    """Same input → byte-identical signal JSON across two runs (signals carry no
    wall-clock; the body is purely a function of disk state)."""
    proj = _proj(tmp_path)
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")

    a = json.dumps(memory_gap.collect(proj), sort_keys=True, ensure_ascii=False)
    b = json.dumps(memory_gap.collect(proj), sort_keys=True, ensure_ascii=False)
    assert a == b


# ---------------------------------------------------------------------------
# 5. exit 0 always, even on malformed inputs (parse_error signal, never crash)
# ---------------------------------------------------------------------------

def test_exit_zero_always(tmp_path):
    """A malformed artifact must not crash the detector: it surfaces a
    `parse_error` signal and the CLI still exits 0 (advisory)."""
    proj = _proj(tmp_path)
    bad = proj / "docs" / "product" / "stories" / "PRD-AUTH-E1-S1.md"
    bad.write_text("---\n: : : not: valid: yaml: [\n---\n# broken\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "memory_gap.py"), "--root", str(proj)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "signals" in out
    assert any(s["type"] == "parse_error" for s in out["signals"]), out["signals"]


# ---------------------------------------------------------------------------
# CLI shape — JSON {signals:[...]} on stdout, exit 0
# ---------------------------------------------------------------------------

def test_cli_emits_signals_json_exit_zero(tmp_path):
    proj = _proj(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "memory_gap.py"), "--root", str(proj)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert isinstance(out.get("signals"), list)
    for s in out["signals"]:
        assert set(s) >= {"type", "severity", "subject", "evidence", "suggested_writer"}
