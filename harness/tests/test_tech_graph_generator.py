"""test_tech_graph_generator.py — the ported tech-graph SVG engine actually renders.

The generator + templates + fixtures were dropped during the original port. This
drives the generator as a subprocess (its real CLI: template-type + output-path,
data piped on stdin) over a shipped fixture and asserts it emits a real SVG, not a
stub. Red before the port (the script + templates were absent).
"""
import subprocess
import sys
from pathlib import Path

import pytest  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]
_TG = _REPO / "harness" / "plugins" / "hs" / "skills" / "tech-graph"
_GEN = _TG / "scripts" / "generate-from-template.py"
_FIXTURE = _TG / "fixtures" / "api-flow-style7.json"



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def test_generator_and_assets_present():
    assert _GEN.exists(), "generate-from-template.py missing"
    assert (_TG / "templates").is_dir() and list((_TG / "templates").glob("*.svg")), "no SVG templates"
    assert list((_TG / "fixtures").glob("*.json")), "no JSON fixtures"
    assert (_TG / "references" / "icons.md").exists(), "icons.md missing"


def test_generator_emits_valid_svg(tmp_path):
    out = tmp_path / "diagram.svg"
    proc = subprocess.run(
        [sys.executable, str(_GEN), "data-flow", str(out)],
        stdin=_FIXTURE.open("rb"),
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"generator failed: {proc.stderr.decode()[:400]}"
    svg = out.read_text(encoding="utf-8")
    assert svg.lstrip().startswith("<svg"), "output is not an SVG root element"
    shapes = sum(svg.count(f"<{tag}") for tag in ("rect", "text", "path", "circle", "line"))
    assert shapes >= 10, f"SVG has too few shapes ({shapes}) — likely a stub"


def test_no_claudekit_or_fireworks_brand_in_engine():
    src = _GEN.read_text(encoding="utf-8")
    low = src.lower()
    assert "claudekit" not in low and "fireworks" not in low, "upstream brand survived in the engine"
