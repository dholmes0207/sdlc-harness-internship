"""test_measure_context_floor.py — the per-turn context-floor gauge.

Measures the byte/token weight of the blocks the harness injects around a session:
the diet first-turn block, the legacy full block (rollback), the voice register, and
the static CLAUDE.md + MEMORY.md files. Acceptance tool for the context diet."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks"))
import measure_context_floor as mcf  # noqa: E402


def test_reports_all_blocks(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "CLAUDE.md").write_text("x" * 500, encoding="utf-8")
    mem = tmp_path / "MEMORY.md"
    mem.write_text("y" * 200, encoding="utf-8")

    r = mcf.measure(tmp_path, memory_index=mem)
    for key in ("first_turn", "full_legacy", "voice_register",
                "claude_md", "memory_index"):
        assert key in r["blocks"], f"missing block: {key}"
    assert r["blocks"]["claude_md"] == 500
    assert r["blocks"]["memory_index"] == 200
    assert r["total_bytes"] == sum(r["blocks"].values())
    assert r["est_tokens"] == round(r["total_bytes"] / 4)


def test_full_block_heavier_than_first_turn(tmp_path):
    (tmp_path / "plans").mkdir()
    r = mcf.measure(tmp_path)
    # The legacy full block carries Session+Rules+Routing+Modularization the diet
    # first-turn block drops — it must be strictly larger.
    assert r["blocks"]["full_legacy"] > r["blocks"]["first_turn"] > 0


def test_missing_files_are_zero_not_crash(tmp_path):
    (tmp_path / "plans").mkdir()
    r = mcf.measure(tmp_path)  # no CLAUDE.md, no memory index
    assert r["blocks"]["claude_md"] == 0
    assert r["blocks"]["memory_index"] == 0


def test_cli_emits_json(tmp_path, capsys):
    (tmp_path / "plans").mkdir()
    (tmp_path / "CLAUDE.md").write_text("z" * 40, encoding="utf-8")
    rc = mcf.main(["--root", str(tmp_path)])
    assert rc == 0
    assert '"claude_md": 40' in capsys.readouterr().out
