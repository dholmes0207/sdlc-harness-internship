"""Test qua HTTP (TestClient) cho 4 route cua app/main.py.

LUAT cho toan bo file nay: MOI assert phai nhin vao THAN phan hoi, khong bao
gio chi assert status code. Ly do: it nhat bon che do hong tra 200 voi noi
dung sai (thu tu route dao nguoc, fixture thieu `with`, response_model chua
ghim, bo loc dung truthiness) - status-only se PASS trong khi API da hong. Day
la luat cho ca nhung che do hong chua ai tim ra, khong phai danh sach dong.

Xem plans/260719-1435-product-search-api/phases/phase-5-api-routes.md cho
tung so lieu OBSERVED va bay da xac minh that tren FastAPI 0.139.2 /
starlette 1.3.1.
"""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models import ProductOut

DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "products.json"


# ---------------------------------------------------------------------------
# R3 - thu tu route (RK2). Dao nguoc /products/search va /products/{product_id}
# KHONG gay 404 - no tra 200 voi than {"hit":"by_id","product_id":"search"}.
# Assert THAN la cach duy nhat bat duoc loi nay (docs/code-standards.md:58-60).
# ---------------------------------------------------------------------------


def test_api_search_route_precedence_body_not_status(client: TestClient) -> None:
    response = client.get("/products/search", params={"q": "may in"})

    body = response.json()
    assert response.status_code == 200
    assert "items" in body
    assert "product_id" not in body
    assert body["total"] == 4


def test_api_products_search_is_not_treated_as_id(client: TestClient) -> None:
    response = client.get("/products/search", params={"q": "a"})

    body = response.json()
    assert "sku" not in body


# ---------------------------------------------------------------------------
# R1 - fold 'd' qua HTTP
# ---------------------------------------------------------------------------


def test_api_search_ngan_da_returns_3_ids(client: TestClient) -> None:
    response = client.get("/products/search", params={"q": "ngan da"})

    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert body["total"] == 3
    assert ids == {
        "refrigerator-358160",
        "refrigerator-363108",
        "refrigerator-363109",
    }


# ---------------------------------------------------------------------------
# R2 - token nguyen ven qua HTTP
# ---------------------------------------------------------------------------


def test_api_search_may_in_returns_exactly_4(client: TestClient) -> None:
    response = client.get("/products/search", params={"q": "may in"})

    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert body["total"] == 4
    assert ids == {
        "printer-318468",
        "printer-318469",
        "printer-357980",
        "printer-357982",
    }


# ---------------------------------------------------------------------------
# R4 - luat gia null qua HTTP
# ---------------------------------------------------------------------------


def test_api_printer_318469_visible_then_hidden(client: TestClient) -> None:
    listed = client.get("/products", params={"page_size": 100})
    listed_body = listed.json()
    listed_ids = {item["id"] for item in listed_body["items"]}
    assert listed_body["total"] == 20
    assert "printer-318469" in listed_ids

    filtered = client.get("/products", params={"page_size": 100, "min_price": 0})
    filtered_body = filtered.json()
    filtered_ids = {item["id"] for item in filtered_body["items"]}
    assert filtered_body["total"] == 19
    assert "printer-318469" not in filtered_ids


# ---------------------------------------------------------------------------
# R5 - pagination
# ---------------------------------------------------------------------------


def test_api_page_size_10_of_20(client: TestClient) -> None:
    response = client.get("/products", params={"page_size": 10})

    body = response.json()
    assert body["total"] == 20
    assert len(body["items"]) == 10


def test_api_page_beyond_total_empty_not_404(client: TestClient) -> None:
    response = client.get("/products", params={"page": 3, "page_size": 10})

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 20
    assert body["items"] == []


# ---------------------------------------------------------------------------
# X3 - response_model duoc noi vao that (RK17)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,params",
    [
        ("/products", {"page_size": 1}),
        ("/products/search", {"q": "may in", "page_size": 1}),
    ],
    ids=["list", "search"],
)
def test_api_response_does_not_leak_raw_fields(
    client: TestClient, path: str, params: dict
) -> None:
    """Ca hai route tra danh sach (`/products` va `/products/search`) phai
    ghim response_model that su - khong chi route `/products`. Bo
    `response_model=` KHOI `search_products` VA doi ham do tra dict tho van
    de 131/131 xanh neu chi mot route duoc khoa (F1)."""
    response = client.get(path, params=params)

    item = response.json()["items"][0]
    for leaked_field in (
        "specifications",
        "availability",
        "image_url",
        "model_code",
        "product_id_web",
    ):
        assert leaked_field not in item
    assert set(item.keys()) == set(ProductOut.model_fields.keys())


def test_api_by_id_does_not_leak_raw_fields(client: TestClient) -> None:
    response = client.get("/products/printer-318469")

    body = response.json()
    assert "specifications" not in body


# ---------------------------------------------------------------------------
# X4 / RK3 - lifespan + /health, canary cho toan bo file
# ---------------------------------------------------------------------------


def test_api_health_products_loaded_is_20(client: TestClient) -> None:
    response = client.get("/health")

    body = response.json()
    # PHAI la len(dataset.products) = 20, TUYET DOI khong phai len(index) = 90
    # (so token phan biet). Xem Requirements #8 phase-5-api-routes.md (VL-13).
    assert body["status"] == "ok"
    assert body["products_loaded"] == 20


def test_api_startup_propagates_dataset_load_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RK13 (docs/code-standards.md:78-79): loi nap dataset trong `lifespan`
    PHAI truyen thang len, KHONG duoc nuot bang try/except roi khoi dong app
    voi index rong (F6). Monkeypatch `app.main.load_dataset` de gia lap loi
    nap - TUYET DOI khong dung toi `data/products.json` that.

    Kiem o cap app (qua `TestClient` truc tiep, khong qua fixture `client`
    dung chung), khong phai cap don vi cua `load_dataset` - day la cap gan
    nhat noi hanh vi "fail fast luc khoi dong" thuc su duoc quan sat: TestClient
    kich hoat lifespan luc `__enter__`, nen loi nap phai lam `with TestClient(...)`
    nem loi thay vi tra ve mot client dang chay voi index rong."""
    import app.main as main_module
    from app.loader import DatasetError

    def _raise_dataset_error() -> None:
        raise DatasetError("gia lap loi nap dataset - khong dung file that")

    monkeypatch.setattr(main_module, "load_dataset", _raise_dataset_error)

    with pytest.raises(DatasetError):
        with TestClient(main_module.app):
            pass


# ---------------------------------------------------------------------------
# RK4 - q khoang trang
# ---------------------------------------------------------------------------


def test_api_q_whitespace_only_returns_422(client: TestClient) -> None:
    response = client.get("/products/search", params={"q": "  "})

    assert response.status_code == 422


def test_api_q_empty_returns_422(client: TestClient) -> None:
    response = client.get("/products/search", params={"q": ""})

    assert response.status_code == 422


def test_api_q_missing_returns_422(client: TestClient) -> None:
    response = client.get("/products/search")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# X5 - q chi dau cau
# ---------------------------------------------------------------------------


def test_api_q_punctuation_only_returns_empty_not_422(client: TestClient) -> None:
    response = client.get("/products/search", params={"q": "---"})

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Validation bien
# ---------------------------------------------------------------------------


def test_api_page_zero_returns_422(client: TestClient) -> None:
    response = client.get("/products", params={"page": 0})

    assert response.status_code == 422


def test_api_page_size_101_returns_422(client: TestClient) -> None:
    response = client.get("/products", params={"page_size": 101})

    assert response.status_code == 422


def test_api_page_size_100_ok(client: TestClient) -> None:
    """Bien tren cua page_size la inclusive.

    Assert vao THAN phan hoi, khong chi status: mot ban chap nhan
    page_size=100 nhung tra items rong hoac total sai van cho 200, nen
    status mot minh khong khoa duoc gi (docs/code-standards.md:58-60).
    """
    response = client.get("/products", params={"page_size": 100})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 20
    assert len(body["items"]) == 20


def test_api_min_price_gt_max_price_returns_422(client: TestClient) -> None:
    on_list = client.get("/products", params={"min_price": 100, "max_price": 10})
    on_search = client.get(
        "/products/search", params={"q": "a", "min_price": 100, "max_price": 10}
    )

    assert on_list.status_code == 422
    assert on_search.status_code == 422


def test_api_negative_min_price_returns_422(client: TestClient) -> None:
    response = client.get("/products", params={"min_price": -1})

    assert response.status_code == 422


def test_api_unknown_query_param_ignored(client: TestClient) -> None:
    """Param la bi BO QUA, khong phai chi "khong bi tu choi".

    Assert vao THAN phan hoi: status 200 mot minh chi chung minh khong tra
    422. Neu `bogus` vo tinh roi vao mot nhanh loc va lam rong ket qua,
    response van la 200 nhung total ve 0 - dung kieu hong tham lang 200 ma
    docs/code-standards.md:58-60 canh bao. total phai giu nguyen 20 va
    page_size mac dinh 10 van duoc ap dung.
    """
    response = client.get("/products", params={"bogus": "x"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 20
    assert len(body["items"]) == 10


# ---------------------------------------------------------------------------
# 404
# ---------------------------------------------------------------------------


def test_api_unknown_product_id_returns_404(client: TestClient) -> None:
    response = client.get("/products/does-not-exist")

    body = response.json()
    assert response.status_code == 404
    assert "detail" in body


def test_api_known_product_id_returns_it(client: TestClient) -> None:
    response = client.get("/products/printer-318469")

    body = response.json()
    assert response.status_code == 200
    assert body["id"] == "printer-318469"


# ---------------------------------------------------------------------------
# Category / brand qua HTTP
# ---------------------------------------------------------------------------


REFRIGERATOR_IDS = {
    "refrigerator-358160",
    "refrigerator-363107",
    "refrigerator-363108",
    "refrigerator-363109",
}


@pytest.mark.parametrize(
    "category_value", ["refrigerator", "tu lanh", "Tủ Lạnh", "TU LANH"]
)
def test_api_category_accepts_slug_and_folded_name(
    client: TestClient, category_value: str
) -> None:
    response = client.get("/products", params={"category": category_value})

    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert body["total"] == 4
    assert ids == REFRIGERATOR_IDS


def test_api_unknown_category_returns_empty(client: TestClient) -> None:
    response = client.get("/products", params={"category": "bogus"})

    assert response.json()["total"] == 0


@pytest.mark.parametrize("brand_value", ["OPPO", "oppo"])
def test_api_brand_case_insensitive(client: TestClient, brand_value: str) -> None:
    response = client.get("/products", params={"brand": brand_value})

    assert response.json()["total"] == 2


# ---------------------------------------------------------------------------
# X1 - chuoi rong qua HTTP
# ---------------------------------------------------------------------------


def test_api_empty_category_returns_zero_not_all(client: TestClient) -> None:
    response = client.get("/products", params={"category": ""})

    assert response.json()["total"] == 0


def test_api_empty_brand_returns_zero_not_all(client: TestClient) -> None:
    response = client.get("/products", params={"brand": ""})

    assert response.json()["total"] == 0


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_api_openapi_and_docs_served(client: TestClient) -> None:
    openapi_response = client.get("/openapi.json")
    docs_response = client.get("/docs")

    assert openapi_response.status_code == 200
    assert docs_response.status_code == 200


def test_api_openapi_lists_four_paths(client: TestClient) -> None:
    response = client.get("/openapi.json")

    paths = set(response.json()["paths"].keys())
    assert paths == {"/health", "/products", "/products/search", "/products/{product_id}"}


# ---------------------------------------------------------------------------
# Tests After
# ---------------------------------------------------------------------------


def test_api_sorted_by_id_ascending(client: TestClient) -> None:
    response = client.get("/products", params={"page_size": 100})

    items = response.json()["items"]
    assert items[0]["id"] == "air-conditioner-335837"
    assert items[-1]["id"] == "tablet-345544"


def test_api_dataset_file_unchanged(client: TestClient) -> None:
    before_stat = DATA_PATH.stat()
    before_sha256 = hashlib.sha256(DATA_PATH.read_bytes()).hexdigest()

    client.get("/products", params={"page_size": 100})
    client.get("/products/search", params={"q": "may in"})
    client.get("/products", params={"category": "refrigerator"})
    client.get("/products/printer-318469")
    client.get("/products/does-not-exist")

    after_stat = DATA_PATH.stat()
    after_sha256 = hashlib.sha256(DATA_PATH.read_bytes()).hexdigest()

    assert before_stat.st_mtime == after_stat.st_mtime
    assert before_stat.st_size == after_stat.st_size
    assert before_sha256 == after_sha256
