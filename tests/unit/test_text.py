"""Test cho app/text.py: accent fold d-aware + tokenizer.

Day la phase rui ro nhat cua plan (xem docs/system-architecture.md Key
decisions #2). unicodedata NFD KHONG dung toi 'd' (khong thuoc category Mn),
nen fold ngay tho lam hong 8/20 blob trong data/products.json. Cac test o
day khoa dung che do hong nay, khong chi khoa hanh vi happy-path.
"""

import json
from pathlib import Path

import pytest

from app.text import fold, tokenize

_DIACRITIC_CASES = [
    ("Tủ Lạnh", "tu lanh"),
    ("Máy lạnh", "may lanh"),
    ("Màn hình máy tính", "man hinh may tinh"),
    ("Máy tính bảng", "may tinh bang"),
    ("Máy in", "may in"),
]


def test_fold_maps_d_stroke_to_d() -> None:
    """R1 - test hoi quy bat buoc #1 cua docs/code-standards.md:53-55.

    Test quan trong nhat bo test: khoa dung cai NFD lam hong.
    """
    assert fold("Ngăn đá") == "ngan da"


def test_fold_d_stroke_uppercase() -> None:
    """Khoa nhanh 'Đ' hoa - map mot chieu 'd' ma quen 'Đ' la loi nua voi hay gap."""
    assert fold("Đá") == "da"


def test_naive_nfd_would_fail_this() -> None:
    """Khoa truc tiep che do hong: neu than ham bi thay bang NFD tran (khong
    map 'd'), test nay do voi thong diep ro rang thay vi im lang sai ket qua.
    """
    assert "đ" not in fold("Ngăn đá")


@pytest.mark.parametrize("raw, expected", _DIACRITIC_CASES)
def test_fold_strips_all_vietnamese_diacritics(raw: str, expected: str) -> None:
    """Bang tham so voi 5 category_name that, OBSERVED tu data/products.json."""
    assert fold(raw) == expected


@pytest.mark.parametrize("raw, _expected", _DIACRITIC_CASES)
def test_fold_is_idempotent(raw: str, _expected: str) -> None:
    """Invariant: fold hai lan phai cho ket qua giong fold mot lan."""
    assert fold(fold(raw)) == fold(raw)


def test_fold_lowercases() -> None:
    """Khoa brand khop khong phan biet hoa thuong (quyet dinh #11).

    OBSERVED: brand trong dataset lan lon casing (Hp, Aoc, OPPO).
    """
    assert fold("SAMSUNG") == "samsung"
    assert fold("Hp") == "hp"
    assert fold("OPPO") == "oppo"


def test_tokenize_splits_on_non_alnum() -> None:
    """Khoa: giu so, cat dau gach/khoang trang."""
    assert tokenize("Tủ Lạnh 4 Cửa - 508L") == {"tu", "lanh", "4", "cua", "508l"}


def test_tokenize_returns_set_not_list() -> None:
    """Khoa kieu tra ve ma phase 4 phu thuoc: tokenize phai tra set, khong phai list."""
    assert isinstance(tokenize("a b"), set)


def test_tokenize_empty_and_whitespace() -> None:
    """Khoa bien: phase 5 dua vao day de quyet q toan khoang trang."""
    assert tokenize("") == set()
    assert tokenize("   ") == set()


def test_tu_tu_homograph_collapses() -> None:
    """Khoa trade-off co chu y (RK11), khong phai bug.

    'tu' va 'tu' (dau hoi vs dau huyen) cung fold thanh 'tu'. Ai do 'sua' cho
    hai chu nay khac nhau se lam do test nay va phai doc ly do trong
    docs/system-architecture.md Constraints & non-goals.
    """
    assert fold("tủ") == fold("từ") == "tu"


def test_no_d_stroke_survives_fold_across_dataset() -> None:
    """Tests-After: nap data/products.json that, fold blob name+brand+category_name
    cua ca 20 san pham, dam bao 0/20 con sot ky tu 'd'/'Đ'.

    OBSERVED: naive NFD -> 8/20 sot; d-aware -> 0/20.
    """
    dataset_path = Path(__file__).resolve().parent.parent.parent / "data" / "products.json"
    with dataset_path.open(encoding="utf-8") as dataset_file:
        dataset = json.load(dataset_file)

    products = dataset["products"]
    assert len(products) == 20

    leftover_count = 0
    for product in products:
        blob = f"{product['name']} {product['brand']} {product['category_name']}"
        folded_blob = fold(blob)
        if "đ" in folded_blob or "Đ" in folded_blob:
            leftover_count += 1

    assert leftover_count == 0
