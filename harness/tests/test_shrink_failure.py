"""shrink_failure — delta-debugging (ddmin) input minimizer.

The reduction core (ddmin) is pure and unit-tested to a known 1-minimal result; one
end-to-end test drives the real script over a file whose failure reproduces only
while a marker line survives, and asserts it shrinks to that single line.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "harness/plugins/hs/skills/debug/scripts/shrink_failure.py"
sys.path.insert(0, str(_SCRIPT.parent))
import shrink_failure as sf  # noqa: E402


# ---- pure: ddmin ---------------------------------------------------------------

def test_ddmin_isolates_required_pair():
    # only the presence of BOTH 3 and 6 is "interesting"; ddmin must reduce to [3, 6].
    items = list(range(8))
    result = sf.ddmin(items, lambda s: 3 in s and 6 in s)
    assert result == [3, 6]


def test_ddmin_single_required_element():
    result = sf.ddmin(list(range(10)), lambda s: 5 in s)
    assert result == [5]


def test_ddmin_everything_required_is_irreducible():
    items = list(range(5))
    result = sf.ddmin(items, lambda s: len(s) == 5)
    assert result == items


def test_ddmin_preserves_order_and_duplicates():
    # duplicates must be handled positionally, not by value membership
    items = ["a", "x", "a", "y", "a"]
    result = sf.ddmin(items, lambda s: s.count("a") >= 2)
    assert result == ["a", "a"]


# ---- end-to-end over the real script ------------------------------------------

def _run(args, cwd, timeout=120):
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          cwd=cwd, capture_output=True, text=True, timeout=timeout)


def test_end_to_end_reduces_to_marker_line(tmp_path):
    f = tmp_path / "input.txt"
    body = ["line%d" % i for i in range(10)] + ["BOOM"] + ["tail%d" % i for i in range(6)]
    f.write_text("\n".join(body) + "\n", encoding="utf-8")
    # "interesting" (still reproduces) iff the BOOM marker survives
    out = _run([str(f), "--", "grep", "-q", "BOOM", str(f)], tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert f.read_text(encoding="utf-8").strip() == "BOOM", f.read_text()
    # original preserved for recovery
    assert (tmp_path / "input.txt.orig").exists()


def test_non_reproducing_input_is_an_error(tmp_path):
    f = tmp_path / "input.txt"
    f.write_text("nothing interesting here\n", encoding="utf-8")
    # command never exits 0 → precondition fails
    out = _run([str(f), "--", "grep", "-q", "BOOM", str(f)], tmp_path)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "reproduce" in (out.stdout + out.stderr).lower()


def test_missing_command_is_usage_error(tmp_path):
    f = tmp_path / "input.txt"
    f.write_text("x\n", encoding="utf-8")
    out = _run([str(f)], tmp_path)
    assert out.returncode == 2, out.stdout + out.stderr


def test_split_join_is_an_exact_inverse():
    # join(split(x)) == x for every input, both modes — no trailing newline added/dropped
    for data in [b"a\nb\nc\n", b"a\nb\nc", b"", b"\n", b"line", b"x\n\ny\n",
                 bytes(range(256))]:
        units, join = sf._split(data, by_char=False)
        assert join(units) == data, ("line", data)
        cunits, cjoin = sf._split(data, by_char=True)
        assert cjoin(cunits) == data, ("char", data)


def test_content_independent_failure_is_rejected(tmp_path):
    # a command that ignores the file (always exits 0) would "reduce" to empty — the
    # failure does not depend on the input. Reject it instead of silently misleading.
    f = tmp_path / "input.txt"
    f.write_text("anything here\n", encoding="utf-8")
    out = _run([str(f), "--", "true"], tmp_path)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "independent" in (out.stdout + out.stderr).lower()
    assert f.read_text(encoding="utf-8") == "anything here\n"  # restored


def test_binary_input_not_corrupted(tmp_path):
    f = tmp_path / "input.bin"
    data = bytes(range(256)) + b"MARKER" + bytes(range(256))
    f.write_bytes(data)
    chk = tmp_path / "chk.py"
    chk.write_text("import sys\n"
                   "sys.exit(0 if b'MARKER' in open(%r,'rb').read() else 1)\n" % str(f))
    out = _run([str(f), "--char", "--", sys.executable, str(chk)], tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    # the backup is byte-identical — no utf-8 'replace' corruption of binary content
    assert (tmp_path / "input.bin.orig").read_bytes() == data
    assert b"MARKER" in f.read_bytes()


def test_existing_orig_backup_is_not_clobbered(tmp_path):
    f = tmp_path / "input.txt"
    f.write_text("x\nBOOM\ny\n", encoding="utf-8")
    (tmp_path / "input.txt.orig").write_text("PRECIOUS", encoding="utf-8")  # pre-existing
    out = _run([str(f), "--", "grep", "-q", "BOOM", str(f)], tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (tmp_path / "input.txt.orig").read_text(encoding="utf-8") == "PRECIOUS"
