"""P2: catalog data port — 8 OSS packs + categoryColors + 19 group stencils.

Tests validate verbatim-copy integrity: base64 tiles present, no aws.json
leakage, categoryColors + groups extracted correctly.
"""
import json
import pytest
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAWIO = REPO_ROOT / "harness" / "plugins" / "hs" / "skills" / "drawio"
CATALOG_DIR = DRAWIO / "data" / "catalog"
CAT_COLORS = DRAWIO / "data" / "category-colors.json"
GROUPS_JSON = DRAWIO / "data" / "groups.json"

CATALOG_PACKS = [
    "observability",
    "bigdata",
    "database",
    "cicd",
    "aiml",
    "containers",
    "databricks",
    "network",
]


# --- RED-phase assertions (files not yet created) ---

@pytest.mark.dev_repo
def test_catalog_files_exist():
    """All 8 catalog JSON files exist."""
    missing = []
    for pack in CATALOG_PACKS:
        path = CATALOG_DIR / f"{pack}.json"
        if not path.exists():
            missing.append(pack)
    assert not missing, f"Missing catalog files: {missing}"


def test_every_icon_has_base64_tile():
    """Every icon in every catalog has style with data:image (offline tile)."""
    for pack in CATALOG_PACKS:
        path = CATALOG_DIR / f"{pack}.json"
        if not path.exists():
            continue  # handled by test_catalog_files_exist
        data = json.loads(path.read_text())
        icons = data.get("icons", [])
        assert len(icons) > 0, f"{pack}: zero icons"
        missing_style = []
        for icon in icons:
            style = icon.get("style", "")
            if "data:image" not in style:
                missing_style.append(icon.get("name", icon.get("label", "?")))
        assert not missing_style, (
            f"{pack}: {len(missing_style)} icons missing base64 tile: {missing_style[:5]}"
        )
        # Catalog icons use image= with base64, not shape= stencil refs.
        # Verify every icon has an image=data:image entry.
        missing_image = []
        for icon in icons:
            style = icon.get("style", "")
            if "image=" not in style:
                missing_image.append(icon.get("name", icon.get("label", "?")))
        assert not missing_image, (
            f"{pack}: {len(missing_image)} icons missing image= entry: {missing_image[:5]}"
        )


@pytest.mark.dev_repo
def test_category_colors_exists():
    """category-colors.json exists with >=9 category→hex mappings."""
    assert CAT_COLORS.exists(), "category-colors.json not found"
    data = json.loads(CAT_COLORS.read_text())
    assert len(data) >= 9, f"Expected >=9 categories, got {len(data)}"
    for cat, hexv in data.items():
        assert isinstance(hexv, str) and hexv.startswith("#"), (
            f"category {cat!r}: expected #hex color, got {hexv!r}"
        )


@pytest.mark.dev_repo
def test_groups_json_exists():
    """groups.json exists with exactly 19 group stencils."""
    assert GROUPS_JSON.exists(), "groups.json not found"
    data = json.loads(GROUPS_JSON.read_text())
    groups = data.get("groups", data) if isinstance(data, dict) else data
    if isinstance(groups, dict):
        groups = groups.get("groups", list(groups.values()))
    assert len(groups) == 19, f"Expected 19 groups, got {len(groups)}"
    for g in groups:
        assert "name" in g, f"Group missing name: {g}"
        assert "style" in g, f"Group missing style: {g.get('name', '?')}"


def test_no_aws_json_icon_leakage():
    """Catalog OSS packs must NOT contain aws.json icons (redundant data)."""
    aws_path = Path("/tmp/drawio-ai-kit/catalog/aws.json")
    if not aws_path.exists():
        # Skip if upstream not available — checked at cook time
        return
    aws_data = json.loads(aws_path.read_text())
    aws_styles = {icon.get("style", "") for icon in aws_data.get("icons", [])}
    for pack in CATALOG_PACKS:
        path = CATALOG_DIR / f"{pack}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for icon in data.get("icons", []):
            style = icon.get("style", "")
            assert style not in aws_styles, (
                f"{pack}: icon {icon.get('name', icon.get('label', '?'))} "
                f"style matches aws.json — leakage detected"
            )


def test_catalog_files_git_tracked():
    """All 10 new data files appear in git ls-files (M2: manifest gate)."""
    for pack in CATALOG_PACKS:
        path = CATALOG_DIR / f"{pack}.json"
        if not path.exists():
            continue
        rel = path.relative_to(REPO_ROOT)
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel)],
            cwd=REPO_ROOT, capture_output=True,
        )
        assert result.returncode == 0, f"{rel} not git-tracked"
    for path in [CAT_COLORS, GROUPS_JSON]:
        if not path.exists():
            continue
        rel = path.relative_to(REPO_ROOT)
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel)],
            cwd=REPO_ROOT, capture_output=True,
        )
        assert result.returncode == 0, f"{rel} not git-tracked"
