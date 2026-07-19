"""test_artifact_io — the shared gate-artifact writer: run_seq stamp + atomic write.

Three gate producers (plan_approval, write_verification, write_review_decision) route
through stamp_and_write so run_seq is stamped in ONE place (D1) and every write is
atomic (same-dir .tmp + os.replace). The load test runs a CONCURRENT reader against the
writer — a green-from-birth sequential loop would pass a non-atomic impl too, so a
negative control proves a non-atomic writer DOES tear under the same reader (RT-F3).
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_io  # noqa: E402
import write_review_decision  # noqa: E402


def test_stamp_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RUN_SEQ", "7")
    p = tmp_path / "verification.json"
    artifact_io.stamp_and_write(p, {"verdict": "PASS"})
    assert json.loads(p.read_text())["run_seq"] == 7


def test_no_env_null_backcompat(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_RUN_SEQ", raising=False)
    p = tmp_path / "verification.json"
    artifact_io.stamp_and_write(p, {"verdict": "PASS"})
    rec = json.loads(p.read_text())
    assert rec["run_seq"] is None          # explicit null, not absent (D1 back-compat)
    assert rec["verdict"] == "PASS"


def test_malformed_env_is_null(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RUN_SEQ", "not-a-number")
    p = tmp_path / "v.json"
    artifact_io.stamp_and_write(p, {"verdict": "PASS"})
    assert json.loads(p.read_text())["run_seq"] is None   # fail-open, not a crash


def test_yaml_format_by_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RUN_SEQ", "3")
    import yaml
    p = tmp_path / "review-decision.yaml"
    artifact_io.stamp_and_write(p, {"verdict": "PASS", "reviewer": "agent:x"})
    rec = yaml.safe_load(p.read_text())
    assert rec["run_seq"] == 3 and rec["verdict"] == "PASS"


def test_atomic_leaves_no_tmp(tmp_path):
    p = tmp_path / "verification.json"
    artifact_io.stamp_and_write(p, {"verdict": "PASS"})
    assert p.exists()
    assert list(tmp_path.glob("*.tmp")) == []          # tmp cleaned via os.replace
    assert list(tmp_path.glob("*.json")) == [p]        # reader sees only the .json


def test_cross_volume_fail_loud(tmp_path, monkeypatch):
    # simulate the tmp landing on a different volume than the target dir: the write
    # must fail loud (no torn/partial final), not silently half-apply.
    p = tmp_path / "verification.json"
    real_stat = os.stat

    def fake_stat(path, *a, **k):
        st = real_stat(path, *a, **k)
        if str(path).endswith(".tmp"):
            class _S:  # mimic a stat_result with a different device
                st_dev = st.st_dev + 1
            return _S()
        return st

    monkeypatch.setattr(artifact_io.os, "stat", fake_stat)
    with pytest.raises(artifact_io.CrossVolumeError):
        artifact_io.stamp_and_write(p, {"verdict": "PASS"})
    assert not p.exists()                              # no torn final written
    assert list(tmp_path.glob("*.tmp")) == []          # tmp cleaned up on failure


# --- load: concurrent reader + negative control (RT-F3) -------------------------

def _spin_reader(directory, stop, corrupt):
    d = Path(directory)
    while not stop.is_set():
        for p in d.glob("*.json"):
            try:
                txt = p.read_text(encoding="utf-8")
            except OSError:
                continue
            if txt == "":
                corrupt.append("empty")
                continue
            try:
                json.loads(txt)
            except ValueError:
                corrupt.append("partial")


def test_1000_writes_zero_corrupt(tmp_path):
    target = tmp_path / "verification.json"
    artifact_io.stamp_and_write(target, {"verdict": "PASS", "i": -1})  # seed so reader has a file
    corrupt, stop = [], threading.Event()
    t = threading.Thread(target=_spin_reader, args=(tmp_path, stop, corrupt))
    t.start()
    try:
        for i in range(1000):
            artifact_io.stamp_and_write(target, {"verdict": "PASS", "i": i,
                                                 "pad": "x" * 4000})
    finally:
        stop.set()
        t.join()
    assert corrupt == [], "atomic writer produced %d torn reads" % len(corrupt)


def test_negative_control_nonatomic_tears(tmp_path):
    # PROVE the reader can catch a torn write: a non-atomic in-place writer (truncate,
    # write in two flushed chunks) MUST produce at least one partial/empty read under
    # the same reader. If this passed with the atomic test, the atomic test is
    # meaningless (green-from-birth).
    target = tmp_path / "verification.json"
    target.write_text('{"seed": true}', encoding="utf-8")
    corrupt, stop = [], threading.Event()
    t = threading.Thread(target=_spin_reader, args=(tmp_path, stop, corrupt))
    t.start()
    try:
        payload = json.dumps({"verdict": "PASS", "pad": "y" * 8000})
        half = len(payload) // 2
        for _ in range(400):
            with open(target, "w", encoding="utf-8") as fh:   # truncates in place
                fh.write(payload[:half])
                fh.flush()
                time.sleep(0.0005)                            # reader window
                fh.write(payload[half:])
            if corrupt:
                break
    finally:
        stop.set()
        t.join()
    assert corrupt, "non-atomic control tore nothing — the reader cannot detect tears, so the atomic test is not meaningful"


# --- write_review_decision producer + three-producer coverage -------------------

def test_write_review_decision_stamps_run_seq(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RUN_SEQ", "11")
    (tmp_path / "artifacts").mkdir()
    rc = write_review_decision.main([str(tmp_path), "--verdict", "PASS",
                                     "--rationale", "no findings",
                                     "--reviewer", "agent:code-reviewer"])
    assert rc == 0
    import yaml
    rec = yaml.safe_load((tmp_path / "artifacts" / "review-decision.yaml").read_text())
    assert rec["run_seq"] == 11
    assert rec["verdict"] == "PASS" and rec["role"] == "reviewer"


def test_all_three_gate_producers_route_through_artifact_io():
    # the stamp lives in ONE place — assert each producer calls stamp_and_write so a
    # future edit cannot silently reintroduce an un-stamped/non-atomic write path.
    for mod in ("write_verification.py", "plan_approval.py", "write_review_decision.py"):
        src = (_SCRIPTS / mod).read_text(encoding="utf-8")
        assert "stamp_and_write" in src, "%s must route through artifact_io.stamp_and_write" % mod
