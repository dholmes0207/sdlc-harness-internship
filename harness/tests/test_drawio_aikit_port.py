"""ai-kit port tests.

Tests verify aws-architecture.md is within cap, 7 OSS logos are present,
logo reference is documented, no catalog JSON, and no demo assets.
"""
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAWIO_SKILL = REPO_ROOT / "harness" / "plugins" / "hs" / "skills" / "drawio"
REFERENCES_DIR = DRAWIO_SKILL / "references"
ASSETS_DIR = DRAWIO_SKILL / "assets"
SHAPES_MD = REFERENCES_DIR / "shapes.md"

OSS_LOGOS = ["hudi", "zeppelin", "debezium", "iceberg", "delta", "dagster", "pinot"]


@pytest.mark.dev_repo
def test_aws_arch_within_cap():
    """references/aws-architecture.md must exist and be <=300 lines."""
    aws_md = REFERENCES_DIR / "aws-architecture.md"
    assert aws_md.exists(), "references/aws-architecture.md not found"
    lines = aws_md.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 300, (
        f"aws-architecture.md is {len(lines)} lines (max 300)"
    )


@pytest.mark.dev_repo
def test_oss_logos_present():
    """All 7 OSS logo PNGs must be present in assets/oss-logos/ and non-empty."""
    logos_dir = ASSETS_DIR / "oss-logos"
    assert logos_dir.exists(), f"assets/oss-logos/ not found at {logos_dir}"
    for name in OSS_LOGOS:
        logo = logos_dir / f"{name}.png"
        assert logo.exists(), f"Missing OSS logo: {logo}"
        assert logo.stat().st_size > 0, f"Empty logo file: {logo}"


@pytest.mark.dev_repo
def test_logo_wire_documented():
    """shapes.md or aws-architecture.md must reference oss-logos/ (not orphan assets)."""
    oss_ref = "oss-logos/"
    shapes_has = SHAPES_MD.exists() and oss_ref in SHAPES_MD.read_text()
    aws_md = REFERENCES_DIR / "aws-architecture.md"
    aws_has = aws_md.exists() and oss_ref in aws_md.read_text()
    assert shapes_has or aws_has, (
        "'oss-logos/' not found in shapes.md or aws-architecture.md — logos are orphan assets"
    )


def test_no_catalog_json():
    """No catalog/*.json files should exist in the skill dir (redundant, dropped)."""
    catalog_dir = DRAWIO_SKILL / "catalog"
    if catalog_dir.exists():
        json_files = list(catalog_dir.glob("*.json"))
        assert not json_files, (
            f"Found catalog JSON files (should be dropped): {json_files}"
        )


def test_no_demo_assets():
    """assets/ must only contain oss-logos/ directory (no demo PNGs from ai-kit)."""
    if not ASSETS_DIR.exists():
        return  # no assets dir at all is also fine
    for item in ASSETS_DIR.iterdir():
        if item.is_dir():
            assert item.name == "oss-logos", (
                f"Unexpected dir in assets/: {item.name} (only oss-logos/ expected)"
            )
        elif item.is_file():
            # No loose files in assets root
            assert False, (
                f"Unexpected file in assets/: {item.name} (only oss-logos/ expected)"
            )
