"""docslib — thư viện chung cho 3 skill docs-*. SSOT = frontmatter + _index/*.yaml.

KHÔNG sáng tạo nội dung, KHÔNG phán chất lượng — chỉ parse / validate / generate view.
"""
from .findings import Finding, Findings, ERROR, WARN, INFO
from .frontmatter import Doc, parse, validate as validate_frontmatter, DOC_TYPES
from .discover import discover, iter_md
from .index import Model, Module, load_model
from .manifest import (
    Manifest, Page, Category, load_manifest, load_manifest_from_yaml, validate_manifest,
)
from .playbook import Playbook, load_playbook, PlaybookError
from .derived import DERIVED_OUTPUT_GLOBS, is_derived_output
from . import capabilities, graph, manifest, playbook, derived

__all__ = [
    "Finding", "Findings", "ERROR", "WARN", "INFO",
    "Doc", "parse", "validate_frontmatter", "DOC_TYPES",
    "discover", "iter_md", "Model", "Module", "load_model",
    "Manifest", "Page", "Category", "load_manifest", "load_manifest_from_yaml", "validate_manifest",
    "Playbook", "load_playbook", "PlaybookError",
    "DERIVED_OUTPUT_GLOBS", "is_derived_output",
    "capabilities", "graph", "manifest", "playbook", "derived",
]


def lib_root():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent
