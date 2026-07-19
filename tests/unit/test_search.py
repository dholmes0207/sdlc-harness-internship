"""Test cho app/search.py: run() - khop AND, loc category/brand/gia, sort,
dem total truoc khi cat trang.

Xem plans/260719-1435-product-search-api/phases/phase-4-search-core.md cho
tung so lieu OBSERVED. Ba bay chinh: (1) is not None thay vi truthiness o
buoc loc category/brand/gia (X1, quyet dinh #15); (2) bien gia inclusive
(X2, quyet dinh #16); (3) dem total TRUOC khi cat trang (R5).
"""

import pytest

from app.loader import build_category_map, load_dataset


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


@pytest.fixture(scope="module")
def category_map(dataset):
    return build_category_map(dataset.categories)


@pytest.fixture(scope="module")
def index(dataset):
    from app.index import build_token_index

    return build_token_index(dataset.products)


def _run(dataset, index, category_map, **kwargs):
    from app.search import run

    return run(dataset, index, category_map, **kwargs)


# ---------------------------------------------------------------------------
# R1 / R2 - fold 'd' va token nguyen ven o tang search
# ---------------------------------------------------------------------------


def test_search_ngan_da_returns_3(dataset, index, category_map) -> None:
    total, items = _run(dataset, index, category_map, q="ngan da")

    ids = {item["id"] for item in items}
    assert total == 3
    assert ids == {
        "refrigerator-358160",
        "refrigerator-363108",
        "refrigerator-363109",
    }


def test_search_may_in_returns_exactly_4(dataset, index, category_map) -> None:
    total, items = _run(dataset, index, category_map, q="may in")

    ids = {item["id"] for item in items}
    assert total == 4
    assert ids == {
        "printer-318468",
        "printer-318469",
        "printer-357980",
        "printer-357982",
    }


def test_search_rejects_substring_semantics(dataset, index, category_map) -> None:
    total, items = _run(dataset, index, category_map, q="may in")

    ids = {item["id"] for item in items}
    assert "printer-357980" in ids
    assert total == 4


# ---------------------------------------------------------------------------
# R4 - luat gia null (docs/code-standards.md:61-62)
# ---------------------------------------------------------------------------


def test_null_price_visible_in_list(dataset, index, category_map) -> None:
    total, items = _run(dataset, index, category_map)

    ids = {item["id"] for item in items}
    assert total == 20
    assert "printer-318469" in ids


def test_null_price_excluded_by_min_price(dataset, index, category_map) -> None:
    total, items = _run(dataset, index, category_map, min_price=0)

    ids = {item["id"] for item in items}
    assert total == 19
    assert "printer-318469" not in ids


def test_null_price_excluded_by_max_price(dataset, index, category_map) -> None:
    total, items = _run(dataset, index, category_map, max_price=999_999_999)

    ids = {item["id"] for item in items}
    assert "printer-318469" not in ids


def test_search_may_in_with_price_filter_returns_3(
    dataset, index, category_map
) -> None:
    total, items = _run(dataset, index, category_map, q="may in", min_price=0)

    ids = {item["id"] for item in items}
    assert total == 3
    assert ids == {"printer-318468", "printer-357980", "printer-357982"}


# ---------------------------------------------------------------------------
# X2 - bien gia inclusive (quyet dinh #16, RK19)
# ---------------------------------------------------------------------------


def test_min_price_boundary_is_inclusive(dataset, index, category_map) -> None:
    """Test DUY NHAT phan biet duoc >= va >. Gia effective thap nhat trong
    dataset dung bang 3290000: >= cho 19, > cho 17 (OBSERVED)."""
    total, _items = _run(dataset, index, category_map, min_price=3_290_000)

    assert total == 19


def test_max_price_boundary_is_inclusive(dataset, index, category_map) -> None:
    """Hai san pham re nhat (effective_price == 3290000) phai co trong ket
    qua khi max_price=3290000 (<=, khong phai <). OBSERVED: dung 2 san pham
    khop gia nay, khong san pham nao thap hon."""
    total, items = _run(dataset, index, category_map, max_price=3_290_000)

    ids = {item["id"] for item in items}
    assert total == 2
    assert ids == {"monitor-335629", "tablet-345544"}


# ---------------------------------------------------------------------------
# R5 - total truoc khi cat trang
# ---------------------------------------------------------------------------


def test_pagination_total_before_slicing(dataset, index, category_map) -> None:
    total, items = _run(dataset, index, category_map, page_size=10)

    assert total == 20
    assert len(items) == 10


def test_page_2_returns_remaining_10(dataset, index, category_map) -> None:
    _total1, items_page1 = _run(dataset, index, category_map, page=1, page_size=10)
    total2, items_page2 = _run(dataset, index, category_map, page=2, page_size=10)

    ids_page1 = {item["id"] for item in items_page1}
    ids_page2 = {item["id"] for item in items_page2}

    assert total2 == 20
    assert len(items_page2) == 10
    assert ids_page1.isdisjoint(ids_page2)


def test_page_beyond_total_returns_empty_not_error(
    dataset, index, category_map
) -> None:
    total, items = _run(dataset, index, category_map, page=3, page_size=10)

    assert total == 20
    assert items == []


def test_total_reflects_filter_not_dataset_total(dataset, index, category_map) -> None:
    """Khoa RK8: neu d["total"]=20 ro ri len thi test nay do."""
    total, _items = _run(dataset, index, category_map, category="refrigerator")

    assert total == 4


# ---------------------------------------------------------------------------
# Sort (quyet dinh #7)
# ---------------------------------------------------------------------------


def test_results_sorted_by_id_ascending(dataset, index, category_map) -> None:
    """page_size=20 de lay toan bo 20 san pham trong mot trang, kiem tra sort
    tren ca dau lan cuoi danh sach."""
    total, items = _run(dataset, index, category_map, page_size=20)

    assert total == 20
    assert items[0]["id"] == "air-conditioner-335837"
    assert items[-1]["id"] == "tablet-345544"


def test_sort_differs_from_file_order(dataset, index, category_map) -> None:
    """OBSERVED: id dau trong file (refrigerator-358160) KHAC id dau sau
    sort - khoa contract doc lap voi thu tu file."""
    _total, items = _run(dataset, index, category_map, page_size=20)

    assert items[0]["id"] != "refrigerator-358160"


# ---------------------------------------------------------------------------
# Loc category dual-key (quyet dinh #10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category_value", ["refrigerator", "tu lanh", "Tủ Lạnh", "TU LANH"]
)
def test_category_filter_accepts_slug_and_folded_name(
    dataset, index, category_map, category_value: str
) -> None:
    total, items = _run(dataset, index, category_map, category=category_value)

    ids = {item["id"] for item in items}
    assert total == 4
    assert ids == {
        "refrigerator-358160",
        "refrigerator-363107",
        "refrigerator-363108",
        "refrigerator-363109",
    }


def test_unknown_category_returns_zero_not_all(dataset, index, category_map) -> None:
    """Khoa che do hong 'khong nhan ra thi bo qua bo loc'."""
    total, _items = _run(dataset, index, category_map, category="bogus")

    assert total == 0


def test_search_plus_category_narrows(dataset, index, category_map) -> None:
    total, _items = _run(
        dataset, index, category_map, q="tu lanh", category="refrigerator"
    )

    assert total == 4


# ---------------------------------------------------------------------------
# X1 - chuoi rong KHONG duoc coi la "khong loc" (quyet dinh #15, RK16)
# ---------------------------------------------------------------------------


def test_empty_category_returns_zero_not_all(dataset, index, category_map) -> None:
    """Test DUY NHAT tach duoc `if category:` khoi `if category is not None:`.
    'bogus' truthy nen ca hai ban implementation deu ap bo loc va cung ra 0 -
    chi chuoi rong (falsy) moi lo ra su khac biet 20 vs 0."""
    total, _items = _run(dataset, index, category_map, category="")

    assert total == 0


def test_whitespace_category_returns_zero(dataset, index, category_map) -> None:
    """Khac ca tren: '   ' truthy, nen no khoa nhanh resolve_category
    fold-roi-miss thay vi nhanh is not None."""
    total, _items = _run(dataset, index, category_map, category="   ")

    assert total == 0


def test_empty_brand_returns_zero_not_all(dataset, index, category_map) -> None:
    total, _items = _run(dataset, index, category_map, brand="")

    assert total == 0


# ---------------------------------------------------------------------------
# Loc brand (quyet dinh #11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("brand_value", ["OPPO", "oppo", "oPPo"])
def test_brand_filter_case_insensitive(
    dataset, index, category_map, brand_value: str
) -> None:
    total, _items = _run(dataset, index, category_map, brand=brand_value)

    assert total == 2


def test_brand_hp_matches_one(dataset, index, category_map) -> None:
    """Dataset luu 'Hp', 'HP' phai khop qua fold khong phan biet hoa thuong."""
    total, _items = _run(dataset, index, category_map, brand="HP")

    assert total == 1


# ---------------------------------------------------------------------------
# Trade-off co chu y (RK11)
# ---------------------------------------------------------------------------


def test_tu_lanh_homograph_returns_8(dataset, index, category_map) -> None:
    total, items = _run(dataset, index, category_map, q="tu lanh")

    refrigerator_ids = {
        item["id"] for item in items if item["id"].startswith("refrigerator-")
    }
    air_conditioner_ids = {
        item["id"] for item in items if item["id"].startswith("air-conditioner-")
    }
    assert total == 8
    assert len(refrigerator_ids) == 4
    assert len(air_conditioner_ids) == 4


def test_dien_returns_zero(dataset, index, category_map) -> None:
    total, _items = _run(dataset, index, category_map, q="dien")

    assert total == 0


# ---------------------------------------------------------------------------
# Tests After
# ---------------------------------------------------------------------------


def test_search_never_mutates_dataset(dataset, index, category_map) -> None:
    """Khoa: sort/loc khong duoc sua tai cho danh sach goc cua dataset."""
    original_ids = [p["id"] for p in dataset.products]

    _run(dataset, index, category_map)
    _run(dataset, index, category_map, category="refrigerator")
    _run(dataset, index, category_map, q="may in", min_price=0)
    _run(dataset, index, category_map, page=2, page_size=5)

    assert len(dataset.products) == 20
    assert [p["id"] for p in dataset.products] == original_ids


def test_search_uses_text_fold_not_local_copy() -> None:
    """A10: search.py phai import fold tu app.text, khong duoc nhan ban cuc
    bo (identity doi tuong ham, khong phai so sanh gia tri)."""
    import app.search
    import app.text

    assert app.search.fold is app.text.fold
