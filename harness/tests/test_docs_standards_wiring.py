"""hs:docs must conform the two free-form standards docs to the section-set template
when it generates or updates them — not write them free-form.

The template SSOT is scaffold_standards.py::_SECTIONS; the wiring is prose in the
init/update workflow references that tells docs-manager to seed from `--print` and
verify with `--check` (BL-118). These tests pin that the wiring stays present.
"""
from pathlib import Path

import pytest

_REFS = (Path(__file__).resolve().parent.parent
         / "plugins" / "hs" / "skills" / "docs" / "references")


@pytest.mark.parametrize("ref", ["init-workflow.md", "update-workflow.md"])
def test_standards_conformance_wired(ref):
    text = (_REFS / ref).read_text(encoding="utf-8")
    assert "scaffold_standards.py" in text, "%s must wire the template helper" % ref
    assert "--check" in text, "%s must instruct verifying conformance with --check" % ref
