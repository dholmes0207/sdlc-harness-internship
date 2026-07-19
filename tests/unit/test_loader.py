"""Test cho app/loader.py: nap envelope data/products.json, fail fast, dual-key
category map, resolve_category, effective_price.

Xem plans/260719-1435-product-search-api/phases/phase-3-loader-models.md cho
tung so lieu OBSERVED (20 san pham, 5 category, 10 key trong category map, 1
san pham printer-318469 co effective_price None).
"""

import hashlib
import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Loader - hinh dang envelope (RK9)
# ---------------------------------------------------------------------------


def test_load_dataset_reads_products_key() -> None:
    """Khoa: doc d["products"], khong lap qua ten key cua dict boc ngoai."""
    from app.loader import load_dataset

    dataset = load_dataset()
    assert len(dataset.products) == 20


def test_load_dataset_reads_categories_key() -> None:
    from app.loader import load_dataset

    dataset = load_dataset()
    assert len(dataset.categories) == 5


def test_loader_ignores_dataset_total_key() -> None:
    """Khoa RK8 ngay tu tang loader: khong cho d["total"]=20 ro ri len response.

    Dataset tra ve KHONG duoc mang thuoc tinh/khoa ten "total" - danh no lai
    cho tang pagination envelope o phase 5, hai khai niem khac nhau.
    """
    from app.loader import load_dataset

    dataset = load_dataset()
    assert not hasattr(dataset, "total")
    # Dataset la NamedTuple/dataclass, kiem luon _fields/_asdict neu co de
    # chan viec them field "total" duoi dang khac.
    if hasattr(dataset, "_asdict"):
        assert "total" not in dataset._asdict()


def test_product_ids_are_unique() -> None:
    """Invariant: khong san pham nao trung id trong du lieu that."""
    from app.loader import load_dataset

    dataset = load_dataset()
    ids = {p["id"] for p in dataset.products}
    assert len(ids) == 20


# ---------------------------------------------------------------------------
# Loader - fail fast (RK13)
# ---------------------------------------------------------------------------


def test_load_dataset_raises_on_missing_file(tmp_path: Path) -> None:
    from app.loader import DatasetError, load_dataset

    missing_path = tmp_path / "khong-ton-tai.json"
    with pytest.raises(DatasetError):
        load_dataset(missing_path)


def test_load_dataset_raises_on_invalid_json(tmp_path: Path) -> None:
    bad_file = tmp_path / "rac.json"
    bad_file.write_text("{ khong phai json hop le [[[", encoding="utf-8")

    from app.loader import DatasetError, load_dataset

    with pytest.raises(DatasetError):
        load_dataset(bad_file)


def test_load_dataset_raises_on_toplevel_list(tmp_path: Path) -> None:
    """Khoa truc tiep RK9: top-level la list thay vi dict boc ngoai."""
    list_file = tmp_path / "list.json"
    list_file.write_text(json.dumps([]), encoding="utf-8")

    from app.loader import DatasetError, load_dataset

    with pytest.raises(DatasetError):
        load_dataset(list_file)


def test_load_dataset_raises_on_missing_products_key(tmp_path: Path) -> None:
    no_products = tmp_path / "no_products.json"
    no_products.write_text(
        json.dumps({"categories": [], "total": 0}), encoding="utf-8"
    )

    from app.loader import DatasetError, load_dataset

    with pytest.raises(DatasetError):
        load_dataset(no_products)


def test_load_dataset_raises_on_empty_products(tmp_path: Path) -> None:
    """Khoa: khong cho khoi dong voi index rong."""
    empty_products = tmp_path / "empty_products.json"
    empty_products.write_text(
        json.dumps({"products": [], "categories": [], "total": 0}),
        encoding="utf-8",
    )

    from app.loader import DatasetError, load_dataset

    with pytest.raises(DatasetError):
        load_dataset(empty_products)


def test_load_dataset_raises_on_duplicate_ids(tmp_path: Path) -> None:
    dup_file = tmp_path / "dup.json"
    dup_file.write_text(
        json.dumps(
            {
                "products": [
                    {"id": "same-id", "name": "A"},
                    {"id": "same-id", "name": "B"},
                ],
                "categories": [],
                "total": 2,
            }
        ),
        encoding="utf-8",
    )

    from app.loader import DatasetError, load_dataset

    with pytest.raises(DatasetError):
        load_dataset(dup_file)


def test_load_dataset_raises_on_product_missing_id(tmp_path: Path) -> None:
    missing_id_file = tmp_path / "missing_id.json"
    missing_id_file.write_text(
        json.dumps(
            {
                "products": [{"name": "khong co id"}],
                "categories": [],
                "total": 1,
            }
        ),
        encoding="utf-8",
    )

    from app.loader import DatasetError, load_dataset

    with pytest.raises(DatasetError):
        load_dataset(missing_id_file)


# ---------------------------------------------------------------------------
# Category map + resolve (quyet dinh #10)
# ---------------------------------------------------------------------------


def test_category_map_has_10_keys() -> None:
    """OBSERVED: chay that tren du lieu that, map co dung 10 key da fold."""
    from app.loader import build_category_map, load_dataset

    dataset = load_dataset()
    category_map = build_category_map(dataset.categories)

    expected_keys = {
        "air-conditioner",
        "man hinh may tinh",
        "may in",
        "may lanh",
        "may tinh bang",
        "monitor",
        "printer",
        "refrigerator",
        "tablet",
        "tu lanh",
    }
    assert set(category_map.keys()) == expected_keys
    assert len(category_map) == 10


@pytest.mark.parametrize(
    "value,expected",
    [
        ("refrigerator", "refrigerator"),
        ("tu lanh", "refrigerator"),
        ("Tủ Lạnh", "refrigerator"),
        ("TU LANH", "refrigerator"),
        ("may in", "printer"),
        ("Máy in", "printer"),
        ("monitor", "monitor"),
    ],
)
def test_resolve_category_accepts_slug_and_display_name(
    value: str, expected: str
) -> None:
    """Bang tham so goi qua resolve_category (khong tra map truc tiep).

    3/7 case nay (Tu Lanh, TU LANH, May in) KHONG the pass neu tra thang
    category_map.get(value) vi gia tri nguoi dung gui len chua duoc fold -
    day chinh la ly do resolve_category ton tai (VL-18).
    """
    from app.loader import build_category_map, load_dataset, resolve_category

    dataset = load_dataset()
    category_map = build_category_map(dataset.categories)

    assert resolve_category(category_map, value) == expected


def test_resolve_category_returns_none_for_unknown() -> None:
    from app.loader import build_category_map, load_dataset, resolve_category

    dataset = load_dataset()
    category_map = build_category_map(dataset.categories)

    assert resolve_category(category_map, "bogus") is None


def test_resolve_category_returns_none_for_empty_string() -> None:
    """Khoa quyet dinh #15: chuoi rong khong duoc coi la "khong loc"."""
    from app.loader import build_category_map, load_dataset, resolve_category

    dataset = load_dataset()
    category_map = build_category_map(dataset.categories)

    assert resolve_category(category_map, "") is None
    assert resolve_category(category_map, "   ") is None


def test_loader_uses_text_fold_not_local_copy() -> None:
    """Guard THAT thay cho grep A10 cu (bi str.maketrans lach voi 0 hit).

    Assert identity doi tuong ham: app.loader.fold PHAI la CUNG MOT ham voi
    app.text.fold, khong phai mot ban sao code giong het.
    """
    import app.loader
    import app.text

    assert app.loader.fold is app.text.fold


# ---------------------------------------------------------------------------
# Effective price (glossary term)
# ---------------------------------------------------------------------------


def test_effective_price_prefers_effective_over_original() -> None:
    from app.loader import effective_price

    product = {"effective_price": 100, "original_price": 200}
    assert effective_price(product) == 100


def test_effective_price_falls_back_to_original() -> None:
    """CHU Y: dict tong hop, khong phai du lieu that.

    OBSERVED trong data/products.json: 0 san pham o trang thai
    (effective_price null VA original_price co) - 19 san pham co
    effective_price, 1 san pham (printer-318469) null ca hai. Nhanh fallback
    la nhanh CHET tren du lieu that; chi dict tu dung nhu o day moi cham
    toi no (VL-23). Dung di tim mot san pham that roi tuong minh doc sai
    dataset - khong co san pham nao nhu vay.
    """
    from app.loader import effective_price

    product = {"effective_price": None, "original_price": 150}
    assert effective_price(product) == 150


def test_effective_price_none_for_printer_318469() -> None:
    """Chan R4 o tang loader: dung 1/20 san pham tra None, id la
    printer-318469."""
    from app.loader import effective_price, load_dataset

    dataset = load_dataset()
    none_price_ids = [
        p["id"] for p in dataset.products if effective_price(p) is None
    ]
    assert none_price_ids == ["printer-318469"]


# ---------------------------------------------------------------------------
# Tests After
# ---------------------------------------------------------------------------


def test_load_dataset_default_path_works_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Khoa DEFAULT_DATA_PATH neo vao __file__, khong vao cwd (VL-19).

    Mot default tuong doi se lam test nay do khi cwd != repo root.
    """
    monkeypatch.chdir(tmp_path)

    from app.loader import load_dataset

    dataset = load_dataset()
    assert len(dataset.products) == 20


def test_loader_never_writes_dataset() -> None:
    """Chan cua A12 o tang loader: mtime + st_size + SHA-256 cua
    data/products.json khong doi sau khi goi load_dataset() nhieu lan.

    Ban ke hoach truoc dung grep source tim open(..., "w") - guard do vo
    dung (khop 0/3 dang ghi thuc te). So SHA-256 bat MOI dang ghi bat ke
    viet bang API nao (VL-16).
    """
    from app.loader import DEFAULT_DATA_PATH, load_dataset

    stat_before = DEFAULT_DATA_PATH.stat()
    sha_before = hashlib.sha256(DEFAULT_DATA_PATH.read_bytes()).hexdigest()

    for _ in range(3):
        load_dataset()

    stat_after = DEFAULT_DATA_PATH.stat()
    sha_after = hashlib.sha256(DEFAULT_DATA_PATH.read_bytes()).hexdigest()

    assert stat_before.st_mtime == stat_after.st_mtime
    assert stat_before.st_size == stat_after.st_size
    assert sha_before == sha_after
