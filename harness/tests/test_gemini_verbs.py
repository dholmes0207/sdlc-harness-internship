"""Verb dispatch + job registry contract (phase 4).

Verbs wrap the P3 chokepoint and log every job to an append-only JSONL under
harness/state/gemini/ (gitignored). Status transitions APPEND (never rewrite a
prior line); background+write is refused (RT-09); the registry lives in state,
never the tracked tree (RT-U3). The gemini engine routes through the conftest
print-transport seam; HARNESS_STATE_DIR redirects the registry to a tmp dir.
"""
import json
import sys
import threading
from pathlib import Path

import pytest
import yaml

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import gemini_companion as gc  # noqa: E402

_BASE = {
    "master": "on", "mode": "partner", "write": "read_only", "stop_review_gate": "off",
    "purposes": {"research": "flash", "scout": "flash", "review": "pro",
                 "critique": "pro", "redteam": "pro", "delegate": "pro", "fix": "pro"},
    "route_all_surface": ["research", "scout"], "overrides": {},
    "timeouts": {"default": 5}, "retry": {"max_attempts": 1, "on_markers": []},
    "secret_scrub": "warn",
}


def _cfg(tmp_path, **over):
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(yaml.safe_dump({**_BASE, **over}, sort_keys=False), encoding="utf-8")
    return str(p)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    # The gemini engine routes through GeminiPrintTransport → the conftest global
    # seam (HARNESS_GEMINI_PRINT_CMD → fake_gemini_print) answers it off the wire;
    # no per-test transport fake needed for the healthy path.
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))


def _jobs_lines():
    reg = gc.JobRegistry()
    return reg.read_all()


# --- T1: a verb writes a stamped, provenance-carrying job record -------------
def test_t1_review_writes_job_record(tmp_path, capsys):
    rc = gc.main(["review", "-p", "check this", "--config", _cfg(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # the advisory stdout envelope MUST carry `result` (the findings), not just
    # provenance — otherwise a relayer returning it verbatim silently drops the
    # findings and reports an empty envelope
    assert "result" in out and out["result"] is not None
    assert "provenance" in out and "job_id" in out
    recs = _jobs_lines()
    done = [r for r in recs if r["status"] == "done"]
    assert done, "expected a done record"
    r = done[-1]
    assert r["provenance"]["reviewer_engine"] == "gemini"
    assert r["actor"] and r["ts"]
    assert r["verb"] == "review"


# --- T2: status is a read; transitions append, never rewrite ----------------
def test_t2_status_append_only(tmp_path, capsys):
    gc.main(["review", "-p", "x", "--config", _cfg(tmp_path)])
    before = Path(gc.JobRegistry().path).read_text()
    recs = _jobs_lines()
    job_id = recs[-1]["job_id"]
    # a single job produced >= 2 records (running -> done): append-only lifecycle
    assert len([r for r in recs if r["job_id"] == job_id]) >= 2
    rc = gc.main(["status", job_id])
    assert rc == 0
    # reading status did NOT mutate any prior line
    assert Path(gc.JobRegistry().path).read_text() == before
    assert "done" in capsys.readouterr().out


# --- T3: result returns content + provenance --------------------------------
def test_t3_result_carries_provenance(tmp_path, capsys):
    gc.main(["review", "-p", "hello", "--config", _cfg(tmp_path)])
    job_id = _jobs_lines()[-1]["job_id"]
    capsys.readouterr()
    rc = gc.main(["result", job_id])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["provenance"]["reviewer_engine"] == "gemini"
    assert "hello" in out["result"]["text"]  # user content carried through the template


# --- T4: background + write is refused (RT-09) ------------------------------
def test_t4_background_write_refused(tmp_path, capsys):
    rc = gc.main(["task", "--background", "--write", "-p", "x", "--config", _cfg(tmp_path)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "background" in err.lower() and "write" in err.lower()


# --- T5: concurrent appends never clobber (flock) ---------------------------
def test_t5_concurrent_append_intact(tmp_path):
    reg = gc.JobRegistry()

    def go(n):
        reg.append({"job_id": "j%d" % n, "verb": "review", "status": "done"})

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    recs = reg.read_all()
    assert len({r["job_id"] for r in recs}) == 8
    # every line is valid JSON (no interleaved/torn write)
    assert all("status" in r for r in recs)


# --- T6: cancel appends a cancelled record ----------------------------------
def test_t6_cancel_appends_cancelled(tmp_path, capsys):
    gc.main(["review", "-p", "x", "--config", _cfg(tmp_path)])
    job_id = _jobs_lines()[-1]["job_id"]
    capsys.readouterr()
    rc = gc.main(["cancel", job_id])
    assert rc == 0
    recs = [r for r in _jobs_lines() if r["job_id"] == job_id]
    assert recs[-1]["status"] == "cancelled"


# --- T7: registry lives under harness/state (gitignored), not the tree ------
def test_t7_registry_in_state(tmp_path):
    reg = gc.JobRegistry()
    p = Path(reg.path).resolve()
    assert "state" in p.parts and "gemini" in p.parts
    assert p.name == "jobs.jsonl"


class _DownTransport:
    """A gemini print transport whose run always fails — partner_call degrades LOUD
    (Degraded carries no content field)."""
    def __init__(self):
        pass

    def run(self, **kw):
        raise gc.AcpError("gemini unreachable")


# --- main() advisory must print a clean envelope on Degraded (no content field) --
def test_main_advisory_degraded_prints_envelope(tmp_path, capsys, monkeypatch):
    # engine pinned so a down primary degrades WITHOUT falling back to a real agy
    # spawn; Degraded has no `.content` → the main() print must not AttributeError.
    monkeypatch.setattr(gc, "GeminiPrintTransport", _DownTransport)
    rc = gc.main(["review", "-p", "x", "--config",
                  _cfg(tmp_path, engine="gemini-print")])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "degraded"
    assert out["result"] is None
    assert out["provenance"]["reviewer_engine"] == "gemini"
    assert rc == 3


def test_main_advisory_inert_prints_envelope(tmp_path, capsys):
    # master=off → Inert (also has no `.content`) → clean envelope, result null, rc 0.
    rc = gc.main(["review", "-p", "x", "--config", _cfg(tmp_path, master="off")])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "inert"
    assert out["result"] is None
    assert rc == 0


def test_main_advisory_ok_still_prints_result(tmp_path, capsys):
    # regression: the healthy path still carries the findings dict in `result`.
    rc = gc.main(["review", "-p", "hello", "--config",
                  _cfg(tmp_path, engine="gemini-print")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["result"] is not None and "hello" in out["result"]["text"]
