"""test_critique_config.py — read/set hs:critique `mode` via the CLI.

critique.mode (advisory|gate) was config-file-only (per-run override: --gate /
--advisory). advisory writes a human report; gate ALSO writes a machine verdict a
stage can block on (enforcement is still a separate stage-policy `requires:` edit —
flipping mode does not by itself block). The writer touches ONLY `mode`; lenses,
loop, and verdict blocks are left intact, and the header is preserved.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import critique_config as cq  # noqa: E402

_SAMPLE = """# critique.yaml — keep this header
mode: advisory

lenses:
  plan: [red-teamer, brainstormer]
  default: [red-teamer]

loop:
  max_rounds: 3
"""


def _write(tmp_path, text=_SAMPLE):
    p = tmp_path / "critique.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_mode(tmp_path):
    assert cq.load_critique(path=_write(tmp_path))["mode"] == "advisory"


def test_load_defaults_advisory_when_absent(tmp_path):
    p = _write(tmp_path, "lenses:\n  default: [red-teamer]\n")
    assert cq.load_critique(path=p)["mode"] == "advisory"


def test_set_mode_gate_preserves_lenses_and_header(tmp_path):
    p = _write(tmp_path)
    cq.save_critique({"mode": "gate"}, path=p)
    body = p.read_text(encoding="utf-8")
    assert "# critique.yaml — keep this header" in body
    assert "red-teamer" in body and "max_rounds" in body  # rest intact
    assert cq.load_critique(path=p)["mode"] == "gate"


def test_set_invalid_mode_refused(tmp_path):
    p = _write(tmp_path)
    with pytest.raises(cq.CritiqueConfigError):
        cq.save_critique({"mode": "blocking"}, path=p)
    assert cq.load_critique(path=p)["mode"] == "advisory"


def test_unknown_knob_refused(tmp_path):
    p = _write(tmp_path)
    with pytest.raises(cq.CritiqueConfigError):
        cq.save_critique({"lenses": []}, path=p)


def test_cli_set_and_read(tmp_path):
    p = _write(tmp_path)
    assert cq.main(["--file", str(p), "--set", "mode=gate"]) == 0
    assert cq.load_critique(path=p)["mode"] == "gate"


def test_cli_bad_mode_nonzero_no_write(tmp_path):
    p = _write(tmp_path)
    assert cq.main(["--file", str(p), "--set", "mode=nope"]) != 0
    assert cq.load_critique(path=p)["mode"] == "advisory"


def test_lock_is_bin_global_not_per_project(tmp_path, monkeypatch):
    """The critique-config lock must live under bin_state_dir() (bin-global),
    not the per-project state_dir(), so concurrent writers to the one shared
    critique.yaml serialize under a global install."""
    import importlib
    import harness_paths
    cq = importlib.import_module("critique_config")
    bin_state = tmp_path / "binstate"
    proj_state = tmp_path / "projstate"
    monkeypatch.setattr(harness_paths, "bin_state_dir", lambda: bin_state)
    monkeypatch.setattr(harness_paths, "state_dir", lambda: proj_state)
    p = tmp_path / "critique.yaml"
    p.write_text("mode: advisory\n", encoding="utf-8")
    cq.save_critique({"mode": "gate"}, path=str(p))
    assert (bin_state / "locks").exists(), "lock dir not under bin_state_dir()"
    assert not proj_state.exists(), "lock leaked into per-project state_dir()"
