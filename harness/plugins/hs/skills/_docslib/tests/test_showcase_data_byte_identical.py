"""P2 — characterization: generate_showcase_data BYTE-IDENTICAL trước/sau refactor.

Lưới an toàn cho data-drive (CLUSTER_NAMES + _CLUSTER_TEXT_FIX → showcase.yaml):
output 4 data-JS phải KHÔNG đổi 1 byte. Snapshot sha256 ở _fixtures/showcase_data_sha256.json
(chụp từ trạng thái TRƯỚC refactor). Khác = FAIL.

VSF-specific characterization: SKIP khi không có docs VSF trong repo này.
"""
import hashlib
import json
from pathlib import Path

import pytest

from docslib import load_model, graph

_REPO = Path(__file__).resolve().parents[6]
_SNAP = Path(__file__).resolve().parent / "_fixtures" / "showcase_data_sha256.json"

pytestmark = pytest.mark.skipif(
    not (_REPO / "docs" / "_index" / "showcase.yaml").is_file()
    or not (_REPO / "docs" / "showcase").is_dir()
    or not _SNAP.is_file(),
    reason="VSF-specific characterization — no showcase docs or snapshot in this repo",
)


def test_showcase_data_byte_identical(tmp_path):
    snap = json.loads(_SNAP.read_text(encoding="utf-8"))
    model = load_model(_REPO / "docs")
    written = graph.generate_showcase_data(model, tmp_path)
    got = {fn: hashlib.sha256((tmp_path / fn).read_bytes()).hexdigest() for fn in written}
    for fn, want in snap.items():
        assert fn in got, f"thiếu output {fn}"
        assert got[fn] == want, f"{fn} byte-KHÁC baseline (refactor làm đổi output!)"


def test_graph_zero_vsf_strings():
    """Sau refactor: graph.py không còn chuỗi VSF hardcode (CLUSTER_NAMES/_CLUSTER_TEXT_FIX)."""
    src = (_REPO / "harness/plugins/hs/skills/_docslib/docslib/graph.py").read_text(encoding="utf-8")
    for needle in ("Acquisition Engine", "Document-Understanding Engine", "Động cơ Thu thập",
                   "reused MOD-07/08", "pre-issuance check gọi MOD-02"):
        assert needle not in src, f"graph.py vẫn chứa chuỗi VSF: {needle!r}"
