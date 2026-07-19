"""Tests for the glossary_capture_nudge hook (the capture B-leg for the glossary).

A thin NUDGE-class wrapper around the `glossary_capture` detector. Mirrors
decision_capture_nudge: it NEVER blocks and NEVER writes the glossary. When a
freshly-registered DEC coins a load-bearing term that is not yet in the glossary
SSOT, it surfaces a one-line advisory (pointing at /hs:remember) and records one
observation, then allows. Properties under test:

  - nudge posture: default OFF (config-gated); even ON it only warns + records.
  - no-op guard: Stop runs the detector ONLY when the session touched-flag is set.
  - DEC-anchored: the signal rides a registered DEC, never free-prose sniffing.
  - throttle: one advisory per session (a coined term is surfaced once, not
    re-nudged on every turn).
  - visible degradation: a broken detector chain emits a degraded audit event.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for d in (_HOOKS, _SCRIPTS):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import hook_runtime  # noqa: E402,F401
from conftest import _git  # noqa: E402

HOOK_PATH = _HOOKS / "glossary_capture_nudge.py"


def _hook_runtime():
    import hook_runtime as hr
    return sys.modules.get("hook_runtime", hr)


def _load_hook():
    spec = importlib.util.spec_from_file_location("glossary_capture_nudge", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_config(path: Path, enabled: bool):
    path.write_text(
        "hooks:\n"
        "  glossary_capture_nudge:\n"
        "    enabled: %s\n" % ("true" if enabled else "false"),
        encoding="utf-8")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    tdir = tmp_path / "tmp"
    logs = tmp_path / "logs"
    for d in (state, tdir, logs):
        d.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    _write_config(cfg, enabled=True)
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("TMPDIR", str(tdir))
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(logs))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.setenv("HARNESS_USER", "tester")
    _hook_runtime()._reset_config_cache()
    yield {"state": state, "tmp": tdir, "cfg": cfg}
    _hook_runtime()._reset_config_cache()


def _reconfig(cfg: Path, enabled: bool):
    _write_config(cfg, enabled=enabled)
    _hook_runtime()._reset_config_cache()


def _repo(tmp_path, coined_term: str, glossary_terms):
    """A committed git repo. docs/decisions.yaml (a DEC coining `coined_term`)
    and docs/glossary.yaml are left UNTRACKED so the detector reads the DEC as a
    this-session change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    docs = repo / "docs"
    docs.mkdir()
    import yaml
    (docs / "glossary.yaml").write_text(
        yaml.safe_dump([{"term": t, "definition": "d", "forbidden": [],
                         "backing": []} for t in glossary_terms],
                       allow_unicode=True), encoding="utf-8")
    (docs / "decisions.yaml").write_text(
        yaml.safe_dump([{"id": "DEC-1", "status": "active", "date": "2026-06-28",
                         "actor": "t", "ts": "2026-06-28T00:00:00+00:00",
                         "title": "Introduce the %s mechanism" % coined_term,
                         "rationale": "We adopt `%s` as the canonical name."
                         % coined_term}], allow_unicode=True), encoding="utf-8")
    return repo


def _stop_payload(proj: Path, session_id="sess-1"):
    return {"session_id": session_id, "cwd": str(proj), "hook_event_name": "Stop"}


def _trace_events(state: Path):
    tdir = state / "trace"
    out = []
    if tdir.is_dir():
        for f in sorted(tdir.glob("trace-*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
    return out


def _events_of(state: Path, name: str):
    return [e for e in _trace_events(state) if e.get("event") == name]


# ---------------------------------------------------------------------------
# detector: pure judgment (no git, no files)
# ---------------------------------------------------------------------------

def test_assess_flags_new_term():
    import glossary_capture as gc
    dec = {"id": "DEC-7", "title": "the frobnicator",
           "rationale": "we coin `frobnicator` for X"}
    sig = gc.assess(True, dec, ["`widget`"])
    assert sig and "frobnicator" in sig["terms"] and sig["dec"] == "DEC-7"


def test_assess_silent_when_term_already_known():
    import glossary_capture as gc
    dec = {"id": "DEC-7", "title": "t", "rationale": "see `widget`"}
    assert gc.assess(True, dec, ["`widget`"]) is None


def test_assess_silent_when_dec_unchanged():
    import glossary_capture as gc
    dec = {"id": "DEC-7", "title": "t", "rationale": "`frobnicator`"}
    assert gc.assess(False, dec, []) is None


def test_extract_terms_ignores_paths_and_dec_ids():
    import glossary_capture as gc
    terms = gc.extract_terms("`widget` and `foo/bar.py` and `DEC-9` and `kebab-term`")
    assert "widget" in terms and "kebab-term" in terms
    assert "foo/bar.py" not in terms and "DEC-9" not in terms


# ---------------------------------------------------------------------------
# hook: nudge posture
# ---------------------------------------------------------------------------

def test_new_coined_term_surfaced_and_recorded(_env, tmp_path, capsys):
    mod = _load_hook()
    proj = _repo(tmp_path, "frobnicator", glossary_terms=["`widget`"])
    mod.set_touched_flag("sess-1")
    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0
    err = capsys.readouterr().err
    assert "frobnicator" in err and "/hs:remember" in err
    assert _events_of(_env["state"], "glossary_capture_observation")


def test_known_term_is_silent(_env, tmp_path, capsys):
    mod = _load_hook()
    proj = _repo(tmp_path, "widget", glossary_terms=["`widget`"])
    mod.set_touched_flag("sess-1")
    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0
    assert capsys.readouterr().err.strip() == ""


def test_noop_when_flag_unset(_env, tmp_path, capsys):
    mod = _load_hook()
    proj = _repo(tmp_path, "frobnicator", glossary_terms=[])
    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0
    assert capsys.readouterr().err.strip() == ""


def test_disabled_hook_is_inert(_env, tmp_path, capsys):
    mod = _load_hook()
    proj = _repo(tmp_path, "frobnicator", glossary_terms=[])
    mod.set_touched_flag("sess-1")
    _reconfig(_env["cfg"], enabled=False)
    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0
    assert capsys.readouterr().err.strip() == ""
    assert _events_of(_env["state"], "glossary_capture_observation") == []


def test_throttle_one_advisory_per_session(_env, tmp_path, capsys):
    mod = _load_hook()
    proj = _repo(tmp_path, "frobnicator", glossary_terms=[])
    mod.set_touched_flag("sess-1")
    mod.handle_stop(_stop_payload(proj), str(proj))
    first = capsys.readouterr().err
    mod.handle_stop(_stop_payload(proj), str(proj))
    second = capsys.readouterr().err
    assert "frobnicator" in first
    assert second.strip() == "", "throttle: only one advisory per session"


def test_degraded_trace_when_detector_missing(_env, tmp_path, monkeypatch):
    mod = _load_hook()
    proj = _repo(tmp_path, "frobnicator", glossary_terms=[])
    mod.set_touched_flag("sess-1")

    def _boom():
        raise ImportError("glossary_capture hidden for the test")
    monkeypatch.setattr(mod, "_import_detector", _boom)
    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0
    assert _events_of(_env["state"], "glossary_capture_degraded")


def test_fail_open_when_detector_raises(_env, tmp_path, monkeypatch):
    mod = _load_hook()
    proj = _repo(tmp_path, "frobnicator", glossary_terms=[])
    mod.set_touched_flag("sess-1")

    class _Boom:
        @staticmethod
        def collect(_root):
            raise RuntimeError("kaboom")
    monkeypatch.setattr(mod, "_import_detector", lambda: _Boom)
    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0  # fail-open: a detector crash never breaks the turn


def test_hook_writes_nothing_into_the_project(_env, tmp_path):
    mod = _load_hook()
    proj = _repo(tmp_path, "frobnicator", glossary_terms=[])
    mod.set_touched_flag("sess-1")
    before = {p.relative_to(proj) for p in proj.rglob("*")}
    mod.handle_stop(_stop_payload(proj), str(proj))
    after = {p.relative_to(proj) for p in proj.rglob("*")}
    assert before == after


def test_default_enabled_ship_tree():
    # The shipped config registers this nudge ON (H1-resolved 260709 ship default:
    # glossary_pointer + glossary_capture + decision_reconcile ON, gemini_stop OFF).
    import hook_runtime as hr
    repo = Path(__file__).resolve().parents[2]
    cfg = repo / "harness" / "data" / "harness-hooks.yaml"
    if not cfg.is_file():
        pytest.skip("shipped config absent")
    os.environ.pop("HARNESS_HOOK_CONFIG", None)
    hr._reset_config_cache()
    try:
        assert hr.hook_enabled("glossary_capture_nudge", "nudge") is True
    finally:
        hr._reset_config_cache()
