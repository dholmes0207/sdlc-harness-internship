"""Khop AND tren token, loc category/brand/gia, sort theo id, dem total roi
cat trang.

Tang thuan tuy - KHONG duoc biet gi ve HTTP (docs/code-standards.md:14-15,
docs/system-architecture.md Components). Ham run() nhan tham so Python
thuong, khong nhan Request.
"""

from typing import Any

from app.loader import Dataset, effective_price, resolve_category
from app.text import fold

from app.index import lookup


def run(
    dataset: Dataset,
    token_index: dict[str, set[str]],
    category_map: dict[str, str],
    *,
    q: str | None = None,
    category: str | None = None,
    brand: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    page: int = 1,
    page_size: int = 10,
) -> tuple[int, list[dict[str, Any]]]:
    """Chay dung 7 buoc theo thu tu bat buoc (docs/system-architecture.md:35-37).

    Tra (total, items). KHONG sua tai cho dataset.products - luon lam viec
    tren list moi.
    """
    # Buoc 1: q is not None -> lookup -> thu hep ve tap id. q is None -> toan bo.
    if q is not None:
        matched_ids = lookup(token_index, q)
        results = [p for p in dataset.products if p["id"] in matched_ids]
    else:
        results = list(dataset.products)

    # Buoc 2: loc category. PHAI dung "is not None", KHONG dung truthiness
    # (quyet dinh #15, RK16) - chuoi rong "" la falsy nhung van la mot bo
    # loc that su, phai resolve va cho ra 0 ket qua, khong duoc bo qua.
    if category is not None:
        resolved_slug = resolve_category(category_map, category)
        # resolved_slug co the la None (khong nhan ra category) - van loc
        # binh thuong, KHONG coi None nhu "khong co bo loc".
        results = [
            p
            for p in results
            if resolved_slug is not None and p.get("category") == resolved_slug
        ]

    # Buoc 3: loc brand. Cung PHAI "is not None", so sanh qua fold khong
    # phan biet hoa thuong (quyet dinh #11).
    if brand is not None:
        folded_brand = fold(brand)
        results = [p for p in results if fold(p["brand"]) == folded_brand]

    # Buoc 4: loc gia - chi khi min_price HOAC max_price is not None. Dung
    # effective_price(p); gia None -> LOAI (quyet dinh #5). Bien inclusive.
    if min_price is not None or max_price is not None:
        filtered: list[dict[str, Any]] = []
        for product in results:
            price = effective_price(product)
            if price is None:
                continue
            if min_price is not None and price < min_price:
                continue
            if max_price is not None and price > max_price:
                continue
            filtered.append(product)
        results = filtered

    # Buoc 5: sort theo id tang dan (quyet dinh #7) - contract doc lap voi
    # thu tu file goc.
    results = sorted(results, key=lambda p: p["id"])

    # Buoc 6: dem total O DAY, TRUOC khi cat trang. Neu dem sau khi cat,
    # total se bien thanh len(items) va pagination sai am tham (R5, RK7) -
    # nguoi goi khong the biet con bao nhieu ket qua khac ngoai trang hien tai.
    total = len(results)

    # Buoc 7: cat trang. Trang vuot total -> tra items rong, KHONG raise,
    # khong 404 (quyet dinh #13).
    start = (page - 1) * page_size
    end = page * page_size
    items = results[start:end]

    return total, items
