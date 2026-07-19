"""Smoke tests for the ported drawio skill scripts.

Verify stdlib-only imports, scripts execute, data is wired,
and no .claude/ path strings leaked in. These are smoke tests (copy-verbatim
port), NOT red-green TDD for the scripts themselves.
"""
import ast
import pytest
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAWIO_SKILL = REPO_ROOT / "harness" / "plugins" / "hs" / "skills" / "drawio"
SCRIPTS_DIR = DRAWIO_SKILL / "scripts"

STDLIB_ALLOWLIST = {
    "os", "sys", "re", "json", "gzip", "argparse", "subprocess", "pathlib",
    "xml", "html", "base64", "zlib", "urllib", "collections", "typing",
    "dataclasses", "math", "tempfile", "shutil", "ast", "shlex",
}

# New scripts (skillgraph.py) use PyYAML — a declared harness dep (not pure stdlib).
# validate.py uses defusedxml (a declared harness dep) instead of stdlib ET to
# safely parse user-supplied .drawio XML files.
# Each entry maps script name -> extra allowed import tops.
EXTRA_ALLOWLIST_BY_SCRIPT = {
    "skillgraph.py": {"yaml"},
    "validate.py": {"defusedxml"},
    "build_pack.py": {"cairosvg"},  # optional dep for SVG→PNG rasterize
    # edit_drawio.py parses user-supplied .drawio XML + fragments through defusedxml
    # (a declared harness dep) to block billion-laughs/XXE, same as validate.py.
    "edit_drawio.py": {"defusedxml"},
}

# Scripts with argparse that should exit 0 on --help
# encode_drawio_url.py uses sys.argv directly (no argparse), so it is excluded
ARGPARSE_SCRIPTS = [
    "autolayout.py",
    "validate.py",
    "shapesearch.py",
    "aiicons.py",
    "pyimports.py",
    "jsimports.py",
    "goimports.py",
    "rustimports.py",
    "pyclasses.py",
    "make_preview_html.py",
    "skillgraph.py",
    "edit_drawio.py",
]

# repair_png.py and encode_drawio_url.py have no argparse
NO_ARGPARSE_SCRIPTS = ["repair_png.py", "encode_drawio_url.py"]



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _all_scripts():
    return sorted(SCRIPTS_DIR.glob("*.py"))


def test_all_scripts_stdlib_only():
    """Every script must only use stdlib imports (plus declared extra deps per script)."""
    assert SCRIPTS_DIR.exists(), f"scripts dir not found: {SCRIPTS_DIR}"
    scripts = _all_scripts()
    assert len(scripts) >= 12, f"expected >=12 scripts, found {len(scripts)}"

    for script in scripts:
        extra = EXTRA_ALLOWLIST_BY_SCRIPT.get(script.name, set())
        allowed = STDLIB_ALLOWLIST | extra
        tree = ast.parse(script.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top in allowed, (
                        f"{script.name}: non-stdlib import {alias.name!r}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top in allowed, (
                        f"{script.name}: non-stdlib from-import {node.module!r}"
                    )


def test_scripts_help_exit0():
    """Each argparse script must exit 0 when run with --help."""
    for name in ARGPARSE_SCRIPTS:
        script = SCRIPTS_DIR / name
        assert script.exists(), f"expected {name} to exist"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"{name} --help exited {result.returncode}\n"
            f"stdout: {result.stdout.decode()}\n"
            f"stderr: {result.stderr.decode()}"
        )


def test_shapesearch_returns_known_shape():
    """shapesearch.py 'aws lambda' must exit 0 and return a style= string."""
    script = SCRIPTS_DIR / "shapesearch.py"
    assert script.exists()
    result = subprocess.run(
        [sys.executable, str(script), "aws lambda"],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"shapesearch exit {result.returncode}\n"
        f"stderr: {result.stderr.decode()}"
    )
    output = result.stdout.decode()
    assert "style=" in output or "shape=" in output, (
        f"expected style= or shape= in output, got:\n{output}"
    )


def test_validate_passes_minimal_drawio():
    """validate.py must exit 0 for a minimal valid .drawio file."""
    minimal = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <mxfile host="drawio" version="26.0.0">
          <diagram name="Page-1">
            <mxGraphModel>
              <root>
                <mxCell id="0" />
                <mxCell id="1" parent="0" />
                <mxCell id="2" value="Hello" style="rounded=1;" vertex="1" parent="1">
                  <mxGeometry x="100" y="100" width="120" height="60" as="geometry" />
                </mxCell>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>
    """)
    script = SCRIPTS_DIR / "validate.py"
    with tempfile.NamedTemporaryFile(suffix=".drawio", mode="w", delete=False) as f:
        f.write(minimal)
        tmp_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, str(script), tmp_path],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"validate exit {result.returncode}\n"
            f"stdout: {result.stdout.decode()}\n"
            f"stderr: {result.stderr.decode()}"
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_no_claude_path_strings():
    """No file in skill dir may contain .claude/skills/ or .claude/hooks/ outside # learn: lines."""
    assert DRAWIO_SKILL.exists(), f"skill dir not found: {DRAWIO_SKILL}"
    violations = []
    for path in DRAWIO_SKILL.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("# learn:"):
                continue
            _BANNED = (".claude/skill" + "s/", ".claude/hook" + "s/")
            if any(b in line for b in _BANNED):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{i}: {stripped!r}")
    assert not violations, (
        "Found .claude/ path strings:\n" + "\n".join(violations)
    )
