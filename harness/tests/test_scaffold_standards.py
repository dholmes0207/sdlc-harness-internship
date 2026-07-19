"""test_scaffold_standards.py — the on-demand TBD skeleton generator for the two
free-form standards docs (system-architecture, code-standards).

The installer never fabricates standards; this is the opt-in helper a deployer
runs to get a structured skeleton to fill. It writes only through the standards
fs_guard zone and refuses to clobber an authored file without --force.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import scaffold_standards  # noqa: E402


@pytest.mark.parametrize("kind", ["system-architecture", "code-standards"])
def test_scaffold_writes_tbd_skeleton(tmp_path, kind):
    rc = scaffold_standards.main(["--type", kind, "--root", str(tmp_path)])
    assert rc == 0
    out = tmp_path / "docs" / (kind + ".md")
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "TBD" in text
    # non-trivial skeleton: clears the installer's 40-char "thin" threshold
    assert len(text.strip()) > 40


def test_scaffold_refuses_to_clobber_without_force(tmp_path):
    out = tmp_path / "docs" / "code-standards.md"
    out.parent.mkdir(parents=True)
    out.write_text("# real standards, do not lose\n" * 5, encoding="utf-8")
    rc = scaffold_standards.main(
        ["--type", "code-standards", "--root", str(tmp_path)])
    assert rc != 0
    assert "real standards, do not lose" in out.read_text()  # untouched


def test_scaffold_force_overwrites(tmp_path):
    out = tmp_path / "docs" / "code-standards.md"
    out.parent.mkdir(parents=True)
    out.write_text("# an authored doc with real content\n" * 5, encoding="utf-8")
    rc = scaffold_standards.main(
        ["--type", "code-standards", "--root", str(tmp_path), "--force"])
    assert rc == 0
    assert "TBD" in out.read_text()
    assert "an authored doc" not in out.read_text()


def test_scaffold_rejects_unknown_type(tmp_path):
    with pytest.raises(SystemExit):  # argparse choices
        scaffold_standards.main(["--type", "bogus", "--root", str(tmp_path)])


# --- --check: conformance of a docs/<type>.md against the _SECTIONS SSOT -------

def _expected_headings(kind):
    return [name for name, _hint in scaffold_standards._SECTIONS[kind][1]]


@pytest.mark.parametrize("kind", ["system-architecture", "code-standards"])
def test_check_conform_exit0(tmp_path, kind):
    doc = tmp_path / (kind + ".md")
    doc.write_text(scaffold_standards.render(kind), encoding="utf-8")  # full skeleton
    rc = scaffold_standards.main(["--type", kind, "--check", str(doc)])
    assert rc == 0


def test_check_missing_section_exit1(tmp_path, capsys):
    kind = "code-standards"
    headings = _expected_headings(kind)
    dropped = headings[1]
    body = "".join("## %s\nx\n\n" % h for h in headings if h != dropped)
    (tmp_path / "d.md").write_text("# Code Standards\n\n" + body, encoding="utf-8")
    rc = scaffold_standards.main(
        ["--type", kind, "--check", str(tmp_path / "d.md")])
    assert rc == 1
    assert dropped in capsys.readouterr().out  # names the missing section


def test_check_extra_section_exit1(tmp_path, capsys):
    kind = "code-standards"
    body = "".join("## %s\nx\n\n" % h for h in _expected_headings(kind))
    (tmp_path / "d.md").write_text(
        "# Code Standards\n\n" + body + "## Bogus Extra\nx\n", encoding="utf-8")
    rc = scaffold_standards.main(
        ["--type", kind, "--check", str(tmp_path / "d.md")])
    assert rc == 1
    assert "Bogus Extra" in capsys.readouterr().out  # names the extra section


def test_check_missing_file_exit2(tmp_path):
    rc = scaffold_standards.main(
        ["--type", "code-standards", "--check", str(tmp_path / "nope.md")])
    assert rc == 2
