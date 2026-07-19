"""test_emit_observation.py — closed-vocab judgment-signal channel.

Lenses today read deterministic data only; there is no channel for a skill to emit an
end-of-work JUDGMENT signal ("evidence I just pulled is thin", "this gate blocked me
twice"). emit_observation appends one deterministically, but the vocabulary is CLOSED:
an out-of-vocab signal is a typo in a human-edited config, so it must fail LOUD (exit 2,
no write), not silently record a bogus signal. Payload is capped so the sink stays small.
"""
import json
import sys
from pathlib import Path

import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import emit_observation as eo  # noqa: E402


def _vocab(tmp_path, names=("thin-evidence", "red-team-reopened", "gate-repeat-block")):
    p = tmp_path / "observation-signals.yaml"
    p.write_text(yaml.safe_dump({"signals": [{"name": n, "description": "d"} for n in names]}),
                 encoding="utf-8")
    return p


def test_emit_in_vocab_appends(tmp_path):
    store = tmp_path / "observations.jsonl"
    rc = eo.main(["--skill", "hs:plan", "--signal", "thin-evidence",
                  "--payload", "evidence pulled was thin",
                  "--signals", str(_vocab(tmp_path)), "--store", str(store)])
    assert rc == 0
    lines = store.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["skill"] == "hs:plan"
    assert rec["signal"] == "thin-evidence"
    assert rec["payload"] == "evidence pulled was thin"
    assert rec.get("actor") and rec.get("ts")


def test_emit_out_of_vocab_exit2_no_write(tmp_path):
    store = tmp_path / "observations.jsonl"
    rc = eo.main(["--skill", "hs:plan", "--signal", "not-a-real-signal",
                  "--payload", "x", "--signals", str(_vocab(tmp_path)), "--store", str(store)])
    assert rc == 2
    assert not store.exists() or store.read_text(encoding="utf-8") == ""


def test_payload_cap_2kb(tmp_path):
    store = tmp_path / "observations.jsonl"
    big = "x" * 2049  # one byte over the 2KB cap
    rc = eo.main(["--skill", "hs:plan", "--signal", "thin-evidence",
                  "--payload", big, "--signals", str(_vocab(tmp_path)), "--store", str(store)])
    assert rc == 2
    assert not store.exists() or store.read_text(encoding="utf-8") == ""


def test_emit_store_io_error_fails_open(tmp_path):
    # observation is telemetry-class: an unwritable store must NOT crash the caller.
    # Pointing --store at a DIRECTORY makes open(...,"a") raise an OSError.
    rc = eo.main(["--skill", "hs:plan", "--signal", "thin-evidence", "--payload", "x",
                  "--signals", str(_vocab(tmp_path)), "--store", str(tmp_path)])
    assert rc == 0  # fail-open (warn), not a traceback crash
