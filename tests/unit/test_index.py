"""Test cho app/index.py: token index dict[token -> set(product_id)] va lookup.

Xem plans/260719-1435-product-search-api/phases/phase-4-search-core.md cho
tung so lieu OBSERVED. Chi index ba truong name + brand + category_name
(quyet dinh #2) - KHONG specifications, KHONG slug, KHONG model_code,
KHONG sku.
"""

from pathlib import Path

import pytest

from app.loader import load_dataset


@pytest.fixture(scope="module")
def products() -> list[dict]:
    return load_dataset().products


# ---------------------------------------------------------------------------
# R1 - fold 'd', tang index (docs/code-standards.md:53-55)
# ---------------------------------------------------------------------------


def test_search_ngan_da_returns_3(products: list[dict]) -> None:
    """R1: 'ngan da' phai khop 'Ngan da' qua fold 'd'. Naive NFD cho 0 hit,
    fold dung cho dung 3 hit (OBSERVED)."""
    from app.index import build_token_index, lookup

    index = build_token_index(products)
    result = lookup(index, "ngan da")

    assert result == {
        "refrigerator-358160",
        "refrigerator-363108",
        "refrigerator-363109",
    }


# ---------------------------------------------------------------------------
# R2 - khop token nguyen ven, khong substring (docs/code-standards.md:56-57)
# ---------------------------------------------------------------------------


def test_search_may_in_returns_exactly_4(products: list[dict]) -> None:
    """R2: 'may in' phai tra dung 4, khong phai 16 (substring vi 'in' nam
    trong 'Inverter'). OBSERVED."""
    from app.index import build_token_index, lookup

    index = build_token_index(products)
    result = lookup(index, "may in")

    assert result == {
        "printer-318468",
        "printer-318469",
        "printer-357980",
        "printer-357982",
    }
    assert len(result) == 4


def test_search_rejects_substring_semantics(products: list[dict]) -> None:
    """Chan bien the prefix/substring: 'printer-357980' phai co trong ket
    qua VA tong so phai dung 4 - khong duoc nhieu hon do 'in' khop substring
    'Inverter'."""
    from app.index import build_token_index, lookup

    index = build_token_index(products)
    result = lookup(index, "may in")

    assert "printer-357980" in result
    assert len(result) == 4


# ---------------------------------------------------------------------------
# Trade-off co chu y (RK11) va gioi han pham vi index (quyet dinh #2)
# ---------------------------------------------------------------------------


def test_tu_lanh_homograph_returns_8(products: list[dict]) -> None:
    """Khoa trade-off co chu y, khong phai bug: 'tu'/'tu' (hoi/huyen) cung
    fold thanh 'tu' nen 'tu lanh' tra 8: 4 tu lanh + 4 may lanh. OBSERVED."""
    from app.index import build_token_index, lookup

    index = build_token_index(products)
    result = lookup(index, "tu lanh")

    refrigerator_ids = {p_id for p_id in result if p_id.startswith("refrigerator-")}
    air_conditioner_ids = {
        p_id for p_id in result if p_id.startswith("air-conditioner-")
    }
    assert len(result) == 8
    assert len(refrigerator_ids) == 4
    assert len(air_conditioner_ids) == 4


def test_dien_returns_zero(products: list[dict]) -> None:
    """Khoa quyet dinh #2: chi index name+brand+category_name, 'dien' khong
    khop du 'dien' co trong specifications. OBSERVED."""
    from app.index import build_token_index, lookup

    index = build_token_index(products)
    result = lookup(index, "dien")

    assert result == set()


def test_index_only_covers_three_fields(products: list[dict]) -> None:
    """Chon mot token chi xuat hien trong specifications (khong co trong
    name/brand/category_name), assert no khong co trong index."""
    from app.index import build_token_index

    index = build_token_index(products)

    # "dien" xuat hien trong specifications (vd "Cong suat dien") nhung
    # khong xuat hien o bat ky name/brand/category_name nao trong dataset.
    assert "dien" not in index


def test_index_excludes_sku_field(products: list[dict]) -> None:
    """Khoa quyet dinh #2 chat hon test tren: "dien" khong con khoa duoc gi
    neu index mo rong blob them slug/sku - token do khong doi du blob co
    them truong nao. Chon token CHI xuat hien trong `sku`, khong o bat ky
    name/brand/category_name nao, va assert no khong co trong index. sku
    "1751098000128" (san pham air-conditioner-335837, xac minh that tu
    data/products.json) la mot chuoi so khong trung token nao khac trong
    dataset."""
    from app.index import build_token_index

    index = build_token_index(products)

    assert "1751098000128" not in index


# ---------------------------------------------------------------------------
# Ngu nghia AND cua lookup
# ---------------------------------------------------------------------------


def test_index_and_semantics(products: list[dict]) -> None:
    """Token co that + token khong ton tai phai giao ra set() (AND, khong
    phai OR)."""
    from app.index import build_token_index, lookup

    index = build_token_index(products)
    result = lookup(index, "may khongtontai12345")

    assert result == set()


def test_lookup_empty_query_returns_empty_set(products: list[dict]) -> None:
    from app.index import build_token_index, lookup

    index = build_token_index(products)
    result = lookup(index, "")

    assert result == set()


def test_lookup_punctuation_only_returns_empty_set(products: list[dict]) -> None:
    """OBSERVED: tokenize("---") = set(), nen lookup phai tra set() ro rang,
    khong phai loi hay toan bo index."""
    from app.index import build_token_index, lookup

    index = build_token_index(products)
    result = lookup(index, "---")

    assert result == set()


def test_lookup_single_token_returns_defensive_copy(products: list[dict]) -> None:
    """Query mot token: `lookup` KHONG duoc tra thang object set song ben
    trong token_index. Vong lap `&` khong chay khi chi co mot token, nen
    ban chua fix tra thang `posting_sets[0]` - cung object voi
    `token_index["ngan"]`. Handler la sync `def` nen FastAPI chay trong
    threadpool; caller nao mutate set tra ve se lam hong index dung chung
    cho MOI request sau do, toan tien trinh, khong nem loi nao (F3)."""
    from app.index import build_token_index, lookup

    index = build_token_index(products)
    result = lookup(index, "ngan")

    assert result is not index["ngan"]

    live_snapshot = set(index["ngan"])
    result.discard(next(iter(result)))

    assert index["ngan"] == live_snapshot
