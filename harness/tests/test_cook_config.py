"""test_cook_config.py — read/write cook.yaml execution knobs via the CLI.

cook.parallel (opt-in multi-agent) + cook.parallel_max (concurrency cap) were
config-file-only until now (override chain: --parallel flag > HARNESS_COOK_PARALLEL
> cook.yaml > default). These pin the validated writer hs:setup drives: it accepts
ONLY the two known knobs, validates types/range, preserves the header, and refuses
junk before any write — so a setup one-liner cannot corrupt the file.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cook_config as cc  # noqa: E402


def _write(tmp_path, text):
    p = tmp_path / "cook.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_defaults_when_keys_absent(tmp_path):
    p = _write(tmp_path, "# header\n{}\n")
    got = cc.load_cook(path=p)
    assert got == {"parallel": False, "parallel_max": 4}


def test_load_reads_values(tmp_path):
    p = _write(tmp_path, "parallel: true\nparallel_max: 8\n")
    got = cc.load_cook(path=p)
    assert got == {"parallel": True, "parallel_max": 8}


def test_set_parallel_true_preserves_header(tmp_path):
    p = _write(tmp_path, "# cook.yaml — keep me\nparallel: false\nparallel_max: 4\n")
    cc.save_cook({"parallel": True}, path=p)
    body = p.read_text(encoding="utf-8")
    assert "# cook.yaml — keep me" in body
    assert cc.load_cook(path=p) == {"parallel": True, "parallel_max": 4}


def test_set_parallel_max_validates_positive_int(tmp_path):
    p = _write(tmp_path, "parallel: false\nparallel_max: 4\n")
    with pytest.raises(cc.CookConfigError):
        cc.save_cook({"parallel_max": 0}, path=p)
    with pytest.raises(cc.CookConfigError):
        cc.save_cook({"parallel_max": "lots"}, path=p)
    # unchanged after a refused write
    assert cc.load_cook(path=p)["parallel_max"] == 4


def test_unknown_knob_refused(tmp_path):
    p = _write(tmp_path, "parallel: false\nparallel_max: 4\n")
    with pytest.raises(cc.CookConfigError):
        cc.save_cook({"bogus": 1}, path=p)


def test_cli_set_and_read(tmp_path):
    p = _write(tmp_path, "parallel: false\nparallel_max: 4\n")
    assert cc.main(["--file", str(p), "--set", "parallel=true",
                    "--set", "parallel_max=6"]) == 0
    assert cc.load_cook(path=p) == {"parallel": True, "parallel_max": 6}


def test_cli_bad_value_nonzero_no_write(tmp_path):
    p = _write(tmp_path, "parallel: false\nparallel_max: 4\n")
    assert cc.main(["--file", str(p), "--set", "parallel_max=-1"]) != 0
    assert cc.load_cook(path=p)["parallel_max"] == 4
