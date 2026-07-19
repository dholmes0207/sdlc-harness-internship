"""Doc data/products.json (mot envelope dict), validate, tra ve du lieu thuan
tuy cho tang search/index.

File la envelope dict, KHONG phai list - san pham nam o key "products",
category o key "categories". Envelope co san key "total" (=20) TRUNG TEN voi
"total" cua pagination envelope (=so ban ghi khop sau loc) - hai khai niem
khac nhau, loader BO QUA d["total"] va Dataset khong mang thuoc tinh nay.

Fail fast tren moi loai du lieu hong (docs/code-standards.md) - khong catch
trong, khong nuot loi.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.text import fold

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "products.json"


class DatasetError(Exception):
    """Loi rieng cho tang loader - de phase sau (main.py) phan biet duoc voi
    loi khac va fail fast luc khoi dong."""


@dataclass(frozen=True)
class Dataset:
    """Du lieu da nap va validate tu envelope. KHONG mang truong "total" -
    xem docstring module."""

    products: list[dict[str, Any]]
    categories: list[dict[str, Any]]


def load_dataset(path: Path | None = None) -> Dataset:
    """Doc + validate file envelope, tra ve Dataset.

    `path` co default, KHONG bat buoc truyen. Default la duong dan TUYET DOI
    neo vao __file__ (DEFAULT_DATA_PATH), khong phai chuoi tuong doi - de
    loader hoat dong dung bat ke cwd tien trinh la gi.
    """
    target_path = path if path is not None else DEFAULT_DATA_PATH

    if not target_path.exists():
        raise DatasetError(f"Khong tim thay file dataset: {target_path}")

    try:
        raw_text = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetError(f"Khong doc duoc file dataset: {target_path}") from exc

    try:
        envelope = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"JSON khong hop le trong {target_path}: {exc}") from exc

    if not isinstance(envelope, dict):
        raise DatasetError(
            f"Top-level cua {target_path} phai la dict (envelope), "
            f"nhan duoc {type(envelope).__name__}"
        )

    if "products" not in envelope:
        raise DatasetError(f"Envelope thieu key 'products': {target_path}")
    if "categories" not in envelope:
        raise DatasetError(f"Envelope thieu key 'categories': {target_path}")

    products = envelope["products"]
    categories = envelope["categories"]

    if not isinstance(products, list) or len(products) == 0:
        raise DatasetError(f"'products' rong hoac khong phai list: {target_path}")

    seen_ids: set[str] = set()
    for product in products:
        product_id = product.get("id")
        if product_id is None:
            raise DatasetError(f"San pham thieu key 'id': {product}")
        if product_id in seen_ids:
            raise DatasetError(f"San pham trung id: {product_id}")
        seen_ids.add(product_id)

    return Dataset(products=products, categories=categories)


def build_category_map(categories: list[dict[str, Any]]) -> dict[str, str]:
    """Bang dual-key: voi moi entry, nap ca slug VA fold(name) -> cung tro
    toi slug (quyet dinh #10).

    Goi app.text.fold - khong cai lai (docs/code-standards.md:42).
    """
    category_map: dict[str, str] = {}
    for category in categories:
        slug = category["slug"]
        name = category["name"]
        category_map[slug] = slug
        category_map[fold(name)] = slug
    return category_map


def resolve_category(category_map: dict[str, str], value: str) -> str | None:
    """Tra category_map.get(fold(value)) - ham nay SO HUU viec fold DAU VAO.

    Key trong map da duoc fold, nhung gia tri nguoi dung gui len thi chua -
    tra thang category_map.get(value) se hong voi input co dau/hoa
    (vi du "Tủ Lạnh", "TU LANH", "Máy in").
    """
    folded_value = fold(value).strip()
    if not folded_value:
        return None
    return category_map.get(folded_value)


def effective_price(product: dict[str, Any]) -> float | None:
    """Thuat ngu da dang ky (docs/glossary.yaml): tra effective_price,
    fallback original_price, None khi ca hai null.

    OBSERVED: dung 1 san pham tra None trong toan dataset - printer-318469.
    """
    price = product.get("effective_price")
    if price is not None:
        return price
    return product.get("original_price")
