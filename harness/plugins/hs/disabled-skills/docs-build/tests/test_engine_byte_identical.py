"""P3 — characterization: showcase build output BYTE-IDENTICAL trước/sau engine-extraction.

Lưới an toàn cho việc rút SSG-logic từ docs/showcase/build.py → ssg_engine.py.
Chạy build.py (đã refactor) → so sha256 mọi file public/ với snapshot baseline
(_fixtures/public_sha256.json, chụp TỪ build.py gốc trước extraction).

1 file khác = FAIL (refactor làm đổi output). Đây là invariant cứng của P3.

VSF-specific characterization: SKIP khi không có docs VSF trong repo này.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_BUILD = _REPO / "docs" / "showcase" / "build.py"
_PUBLIC = _REPO / "docs" / "public"
_SNAP = Path(__file__).resolve().parent / "_fixtures" / "public_sha256.json"

pytestmark = pytest.mark.skipif(
    not (_REPO / "docs" / "showcase" / "build.py").is_file()
    or not (_REPO / "docs" / "public").is_dir()
    or not (_SNAP).is_file(),
    reason="VSF-specific characterization — no showcase docs or snapshot in this repo",
)


def _sha_tree(root: Path) -> dict:
    return {
        str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(root.rglob("*")) if f.is_file()
    }


def test_public_byte_identical():
    snap = json.loads(_SNAP.read_text(encoding="utf-8"))
    # build sạch từ build.py hiện tại
    subprocess.run([sys.executable, str(_BUILD)], check=True, cwd=str(_REPO),
                   capture_output=True)
    got = _sha_tree(_PUBLIC)
    # tập file phải khớp
    assert set(got) == set(snap), (
        f"tập file public/ đổi:\n  thêm={sorted(set(got)-set(snap))}\n  thiếu={sorted(set(snap)-set(got))}"
    )
    diffs = [k for k in snap if got.get(k) != snap[k]]
    assert not diffs, f"{len(diffs)} file public/ byte-KHÁC baseline: {diffs}"
