"""Test cho tai lieu: hop dong OpenAPI cua PaginationEnvelope, va doi chieu
bang curl trong README.md voi hanh vi that qua fixture `client`.

Ca hai test dung lai fixture `client` tu tests/api/conftest.py (phase 5) -
van bat buoc `with TestClient(app) as c:`, xem RK3.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

README_PATH = Path(__file__).resolve().parent.parent.parent / "README.md"


# ---------------------------------------------------------------------------
# Hop dong OpenAPI cua PaginationEnvelope
# ---------------------------------------------------------------------------


def test_openapi_envelope_schema_complete(client: TestClient) -> None:
    response = client.get("/openapi.json")

    body = response.json()
    schema = body["components"]["schemas"]["PaginationEnvelope"]
    assert set(schema["properties"].keys()) == {"total", "page", "page_size", "items"}


# ---------------------------------------------------------------------------
# README curl table phai khop hanh vi that. Guard duy nhat chong so bia
# trong README (VL-21): assert so cap parse duoc TRUOC vong lap.
# ---------------------------------------------------------------------------


def parse_curl_table(readme_text: str) -> list[tuple[str, int]]:
    """Trich (duong dan, total mong doi) tu bang curl trong README.

    Chi nhung dong co ca `curl 'localhost:8000/...'` VA `total: <so>` trong
    cung mot dong bang moi duoc parse - dung 5/9 dong theo Requirements #8.
    """
    pairs: list[tuple[str, int]] = []
    for line in readme_text.splitlines():
        if "curl" not in line or "total:" not in line:
            continue
        path_match = re.search(r"localhost:8000(/\S*?)'", line)
        total_match = re.search(r"total:\s*\*{0,2}(\d+)", line)
        if path_match is None or total_match is None:
            continue
        pairs.append((path_match.group(1), int(total_match.group(1))))
    return pairs


def test_readme_curl_examples_match_reality(client: TestClient) -> None:
    pairs = parse_curl_table(README_PATH.read_text(encoding="utf-8"))
    assert len(pairs) == 5, f"parse được {len(pairs)} cặp, phải là 5"

    for path, expected_total in pairs:
        response = client.get(path)

        body = response.json()
        assert body["total"] == expected_total, (
            f"{path}: total={body['total']}, README ghi {expected_total}"
        )
