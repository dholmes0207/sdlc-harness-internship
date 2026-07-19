"""P3: catalog-aware shape search — shapesearch.py loads catalog + shape-index.

Tests cover: offline brand search, H1 title normalization, H2 signature freeze
(dynamodb exact-match lock), source discrimination in --json output.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHAPESEARCH = REPO_ROOT / "harness/plugins/hs/skills/drawio/scripts/shapesearch.py"
DRAWIO_DIR = REPO_ROOT / "harness/plugins/hs/skills/drawio"


def _search(query, extra_args=None):
    """Run shapesearch with --json, return parsed output or None on no-match."""
    cmd = [sys.executable, str(SHAPESEARCH), query, "--json", "--limit", "20"]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        return None  # no-match exits non-zero
    return json.loads(result.stdout.decode())


def test_clickhouse_offline_tile():
    """Search for 'clickhouse' (OSS catalog-only brand, NOT in shape-index)
    returns >=1 result with data:image tile (offline, no network needed).
    """
    results = _search("clickhouse")
    assert results is not None, "clickhouse returned 0 results"
    assert len(results) >= 1, f"Expected >=1 clickhouse result, got {len(results)}"
    has_offline = any("data:image" in r.get("style", "") for r in results)
    assert has_offline, (
        "No clickhouse result has data:image base64 tile (offline parity missing)"
    )


def test_oss_brands_return_offline():
    """Key OSS brands from catalog return offline-tile results."""
    brands = [
        ("jaeger", "observability"),
        ("kafka", "bigdata"),
        ("postgresql", "database"),
        ("jenkins", "cicd"),
        ("mlflow", "aiml"),
        ("docker", "containers"),
        ("lakehouse", "databricks"),
        ("nginx", "network"),
    ]
    for brand, _pack in brands:
        results = _search(brand)
        assert results is not None, f"{brand}: no results"
        has_image = any("data:image" in r.get("style", "") or "image=" in r.get("style", "")
                        for r in results)
        assert has_image, f"{brand}: no offline tile in results"


def test_title_field_normalized():
    """H1: every catalog icon merged into search pool has 'title' (not 'label').
    No record is missing title/style/w/h — KeyError guard.
    """
    results = _search("datadog")
    assert results is not None, "datadog returned 0 results"
    for r in results:
        assert "title" in r, f"Missing 'title' in {r}"
        assert "style" in r, f"Missing 'style' in {r}"
        assert "w" in r, f"Missing 'w' in {r}"
        assert "h" in r, f"Missing 'h' in {r}"
        assert isinstance(r["title"], str) and r["title"], (
            f"Empty/null title in {r}"
        )


def test_dynamodb_exact_match_locked():
    """H2: 'aws dynamodb' top result MUST be 'DynamoDB' (shape-index exact-match
    title), NOT a catalog-only entry. Signature freeze — shapesearch internals
    unchanged (catalog merge at caller level only).
    """
    results = _search("aws dynamodb")
    assert results is not None, "aws dynamodb returned 0 results"
    top_title = results[0]["title"]
    # The shape-index title is "DynamoDB"; catalog icon label may differ.
    assert top_title == "DynamoDB", (
        f"H2 lock broken: top result is {top_title!r}, expected 'DynamoDB'. "
        f"Catalog merge polluted the top rank."
    )


def test_source_field_in_json():
    """--json output includes 'source' field distinguishing catalog vs shape-index."""
    results = _search("aws lambda")
    assert results is not None, "aws lambda returned 0 results"
    sources = {r.get("source", "missing") for r in results}
    # At minimum shape-index should appear; catalog source is bonus
    assert "shape-index" in sources, (
        f"Expected 'shape-index' source in results, got sources: {sources}"
    )


def test_catalog_discoverable_with_shape_search_tag():
    """Searching a tag that exists ONLY in catalog (not shape-index) returns results."""
    # 'jaeger' is in observability catalog, unlikely in shape-index
    results = _search("jaeger")
    assert results is not None, "jaeger returned 0 results"
    # Must have at least one catalog-sourced result
    catalog_results = [r for r in results if r.get("source") == "catalog"]
    assert len(catalog_results) >= 1, (
        f"Expected catalog-sourced result for 'jaeger', "
        f"got {len(catalog_results)} catalog results"
    )
