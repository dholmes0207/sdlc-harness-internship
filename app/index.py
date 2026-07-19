"""Dung token index dict[token -> set(product_id)] va tra cuu AND.

Chi index BA truong name + brand + category_name (quyet dinh #2, OBSERVED
docs/system-architecture.md Key decisions #1). KHONG specifications, KHONG
slug, KHONG model_code, KHONG sku - "dien" trong specifications se KHONG
khop, day la he qua da biet va chap nhan.

Thuat ngu dang ky: "token index" (docs/glossary.yaml). Hai bien the ten goi bi
cam liet o do KHONG duoc xuat hien trong module nay.
"""

from typing import Any

from app.text import tokenize


def build_token_index(products: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Voi moi san pham, dung blob name + " " + brand + " " + category_name,
    tokenize, roi map moi token -> set chua product["id"].

    Chi ba truong nay - xem docstring module.
    """
    token_index: dict[str, set[str]] = {}
    for product in products:
        blob = f"{product['name']} {product['brand']} {product['category_name']}"
        for token in tokenize(blob):
            token_index.setdefault(token, set()).add(product["id"])
    return token_index


def lookup(token_index: dict[str, set[str]], query: str) -> set[str]:
    """Tokenize query, giao (AND) cac posting set - quyet dinh #3.

    Khop token NGUYEN VEN qua tra cuu dict, khong prefix/substring (DEC-1).
    Query khong co token nao (rong, chi khoang trang, chi dau cau nhu "---")
    -> tra set() ro rang.
    """
    query_tokens = tokenize(query)
    if not query_tokens:
        return set()

    posting_sets = [token_index.get(token, set()) for token in query_tokens]
    result = set(posting_sets[0])
    for posting_set in posting_sets[1:]:
        result = result & posting_set
    return result
