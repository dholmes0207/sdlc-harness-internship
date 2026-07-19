"""test_migrate_voice_output.py — output.yaml is rewritten in canonical key order."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_output_yaml_written_in_canonical_order(tmp_path):
    import migrate_voice_output as mvo
    tv = tmp_path / "terminal-voice.yaml"
    out = tmp_path / "output.yaml"
    tv.write_text("output_style: 3\n", encoding="utf-8")
    out.write_text("language: vi\n", encoding="utf-8")
    mvo.migrate(str(tv), str(out), apply=True, backup=False)
    body = out.read_text(encoding="utf-8")
    # canonical order puts language before code_style (alphabetical would invert)
    assert body.index("language:") < body.index("code_style:")
