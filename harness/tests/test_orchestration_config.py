"""orchestration_config loader — round-trip, fail-open, clamp formula.

orchestration.yaml holds the cross-cutting fan-out enforcement caps
(group_cap / batch_consolidate / early_write). This module is the single reader
and the single home of the clamp formula (DRY). An absent file resolves to the
shipped defaults (old behaviour); a malformed file raises so the CLI can point
at the typo, while any gate consulting it wraps the call and treats a raise as a
no-op.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "harness" / "scripts" / "orchestration_config.py"

sys.path.insert(0, str(_ROOT / "harness" / "scripts"))

from orchestration_config import (  # noqa: E402
    OrchestrationConfigError,
    group_cap,
    load_orchestration,
)


def _write(tmp_path, text):
    p = tmp_path / "orchestration.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_absent_file_returns_default(tmp_path):
    missing = tmp_path / "nope.yaml"
    cfg = load_orchestration(path=missing)
    assert cfg["group_cap"]["base"] == 8
    assert cfg["group_cap"]["ceiling"] == 10
    assert cfg["group_cap"]["floor"] == 1
    assert cfg["early_write"]["required"] is True


def test_group_cap_formula():
    # clamp(min(base, concerns), floor, ceiling)
    assert group_cap(3) == 3            # min(8,3)=3
    assert group_cap(20) == 8           # base clamps
    assert group_cap(0) == 1            # floor
    # ceiling bites when base is raised above it
    cfg = {"group_cap": {"base": 15, "ceiling": 10, "floor": 1}}
    assert group_cap(12, cfg=cfg) == 10  # min(15,12)=12 -> ceiling 10


def test_malformed_raises(tmp_path):
    p = _write(tmp_path, '"just a scalar"\n')
    with pytest.raises(OrchestrationConfigError):
        load_orchestration(path=p)


def test_roundtrip_nondefault_value(tmp_path):
    p = _write(tmp_path, "group_cap:\n  base: 5\n  ceiling: 10\n  floor: 1\n")
    cfg = load_orchestration(path=p)
    assert cfg["group_cap"]["base"] == 5
    assert group_cap(20, cfg=cfg) == 5  # min(5,20)=5


def test_early_write_required_bool_both_halves(tmp_path):
    p_true = _write(tmp_path, "early_write:\n  required: true\n")
    assert load_orchestration(path=p_true)["early_write"]["required"] is True
    p2 = tmp_path / "off.yaml"
    p2.write_text("early_write:\n  required: false\n", encoding="utf-8")
    assert load_orchestration(path=p2)["early_write"]["required"] is False


def test_group_cap_cli_resolves():
    out20 = subprocess.run(
        [sys.executable, str(_SCRIPT), "--group-cap", "20"],
        capture_output=True, text=True, check=True)
    assert out20.stdout.strip() == "8"
    out3 = subprocess.run(
        [sys.executable, str(_SCRIPT), "--group-cap", "3"],
        capture_output=True, text=True, check=True)
    assert out3.stdout.strip() == "3"


def test_show_emits_json():
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "--show"],
        capture_output=True, text=True, check=True)
    import json
    cfg = json.loads(out.stdout)
    assert cfg["group_cap"]["base"] == 8
