---
phase: 5
title: "Api Routes"
status: pending
plan: 260719-1435-product-search-api
created: 2026-07-19
harness_version: 5.3.0
harness_kit_digest: 251ed307796039124b44d71759b3f62d8bb9135c4bf3053156e38798587a50a8
harness_schema_version: 1.0
---

# Phase 5 — Api Routes

## Overview

`app/main.py` — FastAPI app, `lifespan` nạp dữ liệu + dựng token index, **4
route theo đúng thứ tự**, và query model có validator chặn `q` toàn khoảng
trắng.

Đây là phase **duy nhất** được import `fastapi` (`docs/code-standards.md:14-15`).
Cũng là nơi tập trung các chế độ **hỏng thầm lặng trả `200`** — API trả `200` với
kết quả sai hoặc rỗng, nên **không** test nào ở đây được phép chỉ assert status
code. Đây là **luật cho mọi test API của phase này**, áp cho cả những chế độ hỏng
chưa ai tìm ra — **không** phải một danh sách đóng cần đếm cho khớp.

Các trường hợp đã biết dưới đây đều được chạy lại thật trong phiên lập kế hoạch
này (FastAPI 0.139.2 / starlette 1.3.1), không phải suy luận:

| # | Chế độ hỏng | Bằng chứng OBSERVED |
|---|---|---|
| RK2 | `{product_id}` khai trước `search` | `GET /products/search` → **`200`** `{"hit":"by_id","product_id":"search"}` — **không phải 404** |
| RK4 | `Query(min_length=1)` không chặn khoảng trắng | `?q=%20%20` → **`200`**. Thêm `field_validator` → **422** |
| RK3 | `TestClient(app)` không dùng context manager | `lifespan` **không chạy** → index rỗng → `GET /products` trả `200 {"total": 0, "items": []}`. Dùng `with` → `total` đúng |

**Phụ thuộc:** phase 4.

## Files

**Create**

- `app/main.py`
- `tests/api/conftest.py`
- `tests/api/test_routes.py`

Không chạm file nào của phase khác.

## Requirements

### Thứ tự route — load-bearing

1. Khai báo **đúng thứ tự này**, `/products/search` **TRƯỚC**
   `/products/{product_id}` (quyết định #9, `docs/system-architecture.md:77-79`):

   ```python
   @app.get("/health")                    # 1
   @app.get("/products")                  # 2
   @app.get("/products/search")           # 3  <-- PHẢI trước #4
   @app.get("/products/{product_id}")     # 4
   ```

   Đặt comment cảnh báo ngay trên route #3 và #4, ghi rõ: đảo thứ tự **không**
   gây 404, nó trả **200** với thân sai.

### Query models (quyết định D-A ở plan.md)

2. `ListParams(BaseModel)` — `category`, `brand`, `min_price` (`ge=0`),
   `max_price` (`ge=0`), `page` (`ge=1`, default 1), `page_size` (`ge=1`,
   `le=100`, default 10). Có `@model_validator(mode="after")` raise `ValueError`
   khi `min_price > max_price` → **422** native (quyết định #12).

3. `SearchParams(ListParams)` — thêm `q: str = Field(min_length=1)` và
   `@field_validator("q")` raise `ValueError` khi `not v.strip()` → **422**
   (quyết định #4, RK4).

4. **`q` phải nằm BÊN TRONG model**, không phải param rời.

   OBSERVED, bẫy đã probe thật: trộn
   `def search(q: Annotated[str, Query(...)], f: Annotated[Filters, Query()])`
   khiến FastAPI coi `f` là field bắt buộc tên `"f"` và trả **422**
   `{"loc": ["query", "f"], "msg": "Field required"}` cho **mọi** request, kể cả
   request hợp lệ (RK10). Dùng kế thừa, không trộn.

5. **Không** đặt `model_config = {"extra": "forbid"}`. OBSERVED: bật lên thì
   `?bogus=x` trả 422 — hành vi ngoài hợp đồng đã khoá (xem Out of scope).

6. Dùng `Annotated[SearchParams, Query()]` làm tham số duy nhất của handler.

### Lifespan

7. `lifespan` qua `@asynccontextmanager`: gọi `load_dataset()` **không tham số**
   (chữ ký P3: `path: Path | None = None`, default neo `__file__` — VL-19), dựng
   `build_token_index()` + `build_category_map()`, cất vào `app.state`.
   **Không** try/except nuốt lỗi — lỗi nạp phải làm app chết
   (`docs/code-standards.md:78-79`, RK13).

### Response

8. `/health` → `HealthOut`
   `{"status": "ok", "products_loaded": len(dataset.products)}` → **20**.

   **`products_loaded` = `len(dataset.products)`, TUYỆT ĐỐI không phải
   `len(index)`.** OBSERVED: `len(index)` = **90** (số token phân biệt),
   `len(products)` = **20**. Bản kế hoạch trước ghi "`len(index)` thật, phải =
   20" — **sai số học**: implementation làm đúng theo chữ sẽ trả **90**, rồi
   test assert 20 và đỏ **sai lý do**, khiến người sửa đi chữa nhầm chỗ (VL-13).

   Ý đồ canary vẫn giữ nguyên: RK3 (thiếu `with`) và RK13 (nạp hỏng) đều kéo
   `len(dataset.products)` về **0**, nên endpoint rẻ nhất vẫn bắt được cả hai.

9. **Mọi route PHẢI ghim `response_model=`** (quyết định D-D, RK17):

   ```python
   @app.get("/health", response_model=HealthOut)
   @app.get("/products", response_model=PaginationEnvelope)
   @app.get("/products/search", response_model=PaginationEnvelope)
   @app.get("/products/{product_id}", response_model=ProductOut)
   ```

   **Vì sao đây là bắt buộc, không phải trang trí:** `search.run()` trả
   `list[dict]` **thô**. Không ghim `response_model` thì FastAPI serialize thẳng
   dict và `ProductOut` **có thể được viết hoàn hảo mà không bao giờ được nối
   vào** — không test nào ở bản kế hoạch trước bắt được.

   OBSERVED trên sản phẩm thật: dict thô **18** trường vs `ProductOut` **13** →
   rò đúng **5** trường (`availability`, `image_url`, `model_code`,
   `product_id_web`, **`specifications`**), payload phình **2.24x** (1177 → 525
   byte). `specifications` bị plan tuyên bố out-of-scope **ba** lần.

10. `/products/{product_id}` → `ProductOut`, hoặc
    `HTTPException(404, detail=...)` khi không thấy. OBSERVED: cho thân
    `{"detail": "..."}`.
11. Route chỉ **điều phối**: parse params → gọi `search.run()` → bọc envelope.
    **Không** logic lọc/sort/phân trang trong `main.py` — nó thuộc phase 4.
    Route **không** tự fold/resolve `category`: truyền thẳng giá trị thô xuống
    `search.run()`, nơi đó gọi `resolve_category()` của P3 (D-C).

## Implementation Steps

1. Viết `tests/api/conftest.py` + `tests/api/test_routes.py` → chạy → **đỏ**.
2. `app/main.py`: `ListParams`, `SearchParams`.
3. `lifespan` + `FastAPI(lifespan=lifespan)`.
4. Bốn route **theo đúng thứ tự** ở Requirements #1, kèm comment cảnh báo.
5. Chạy lại → xanh.

## TDD

### Fixture bắt buộc — đọc trước khi viết bất kỳ test API nào

`tests/api/conftest.py` **PHẢI** dùng context manager:

```python
@pytest.fixture
def client():
    with TestClient(app) as c:      # <-- 'with' là BẮT BUỘC
        yield c
```

**Đây là bẫy đã xác minh, không phải khuyến nghị.** OBSERVED: `TestClient(app)`
trần **không kích hoạt `lifespan`** → index rỗng → `GET /products` trả
`200 {"total": 0, "items": []}`. Mọi assert đếm sẽ sai mà **không có lỗi nào
bắn ra**. Với `with`: `total` đúng.

**Cấm** khởi tạo `TestClient` trực tiếp trong thân test. Mọi test API đi qua
fixture `client`.

### Tests-before (RED)

**R3 — thứ tự route, assert THÂN phản hồi (`docs/code-standards.md:58-60`):**

- [ ] `test_api_search_route_precedence_body_not_status` — `GET /products/search?q=may in`
      → assert `r.status_code == 200` **VÀ** `"items" in body` **VÀ**
      `"product_id" not in body` **VÀ** `body["total"] == 4`.
      Nếu route bị đảo, status vẫn **200** nhưng body là
      `{"hit":"by_id","product_id":"search"}` — assert body là cái duy nhất bắt được.
- [ ] `test_api_products_search_is_not_treated_as_id` — assert
      `GET /products/search?q=a` **không** trả thân của `/products/{product_id}`
      (không có khoá `sku` ở tầng ngoài cùng).

**R1 — fold `đ` qua HTTP (`docs/code-standards.md:53-55`):**

- [ ] `test_api_search_ngan_da_returns_3_ids` — `?q=ngan da` → `total == 3` và
      tập id **đúng** `{refrigerator-358160, refrigerator-363108,
      refrigerator-363109}`. OBSERVED.

**R2 — token nguyên vẹn qua HTTP (`docs/code-standards.md:56-57`):**

- [ ] `test_api_search_may_in_returns_exactly_4` — `?q=may in` → `total == 4`,
      tập id đúng `{printer-318468, printer-318469, printer-357980,
      printer-357982}`. **Không** 16. OBSERVED.

**R4 — luật giá null qua HTTP (`docs/code-standards.md:61-62`):**

- [ ] `test_api_printer_318469_visible_then_hidden` — một test, hai lần gọi:
      `GET /products?page_size=100` → `total == 20`, `printer-318469` **có**
      trong `items`; `GET /products?page_size=100&min_price=0` → `total == 19`,
      `printer-318469` **không** có. OBSERVED.

**R5 — pagination (`docs/code-standards.md:64-65`):**

- [ ] `test_api_page_size_10_of_20` — `GET /products?page_size=10` →
      `total == 20` **và** `len(items) == 10`.
- [ ] `test_api_page_beyond_total_empty_not_404` — `?page=3&page_size=10` →
      status **200**, `total == 20`, `items == []` (quyết định #13).

**X3 — `response_model` được nối vào thật (quyết định D-D, RK17):**

- [ ] `test_api_response_does_not_leak_raw_fields` — `GET /products?page_size=1`
      → assert **`"specifications" not in body["items"][0]`**, và tương tự cho
      `availability`, `image_url`, `model_code`, `product_id_web`.
      Assert thêm `set(body["items"][0]) == ProductOut.model_fields.keys()` —
      **đúng 13** trường.

      Khoá chế độ hỏng: `ProductOut` viết hoàn hảo nhưng **không bao giờ được
      nối vào** vì route quên `response_model=`. `search.run()` trả dict thô
      **18** trường; không có test này thì rò 5 trường + payload **2.24x** mà
      không gì đỏ (VL-17).
- [ ] `test_api_by_id_does_not_leak_raw_fields` — `GET /products/printer-318469`
      → `"specifications" not in body`. Route by-id có đường serialize riêng, nên
      phải khoá riêng.

**X4 / RK3 — lifespan + `/health` (bẫy đã xác minh):**

- [ ] `test_api_health_products_loaded_is_20` — `GET /health` →
      `products_loaded == **20**`.

      **Là `len(dataset.products)`, không phải `len(index)`.** OBSERVED:
      `len(index)` = **90**. Nếu implementation trả 90, test này đỏ và thông
      điệp phải chỉ đúng vào chỗ sai — xem Requirements #8 (VL-13).

      Đây cũng là **canary** cho toàn bộ file test API: fixture quên `with` →
      giá trị về **0** → đỏ ngay.

**RK4 — `q` khoảng trắng (quyết định #4):**

- [ ] `test_api_q_whitespace_only_returns_422` — `?q=%20%20` → **422**.
      OBSERVED: chỉ `min_length=1` thì trả **200**.
- [ ] `test_api_q_empty_returns_422` — `?q=` → **422**.
- [ ] `test_api_q_missing_returns_422` — `/products/search` không có `q` → **422**.

**X5 — `q` chỉ dấu câu (quyết định chốt ở VL-22):**

- [ ] `test_api_q_punctuation_only_returns_empty_not_422` — `?q=---` →
      status **200**, `total == 0`, `items == []`.

      **Không** phải 422: `"---".strip()` là truthy nên nó **hợp lệ** với
      validator non-blank, khác hẳn `q="  "`. OBSERVED: `tokenize("---")` =
      `set()` → không token nào để AND → 0 kết quả. Hành vi này phải được
      **định nghĩa** chứ không để rơi vào ngẫu nhiên của implementation.

**Validation biên (quyết định #6, #12):**

- [ ] `test_api_page_zero_returns_422` — `?page=0` → **422**. OBSERVED.
- [ ] `test_api_page_size_101_returns_422` — `?page_size=101` → **422**. OBSERVED.
- [ ] `test_api_page_size_100_ok` — `?page_size=100` → **200**. Khoá biên trên
      là **inclusive**.
- [ ] `test_api_min_price_gt_max_price_returns_422` — `?min_price=100&max_price=10`
      → **422**, trên **cả** `/products` lẫn `/products/search`. OBSERVED.
- [ ] `test_api_negative_min_price_returns_422` — `?min_price=-1` → **422**.
- [ ] `test_api_unknown_query_param_ignored` — `?bogus=x` → **200** (không phải
      422). Khoá quyết định "không bật `extra: forbid`".

**404 (`docs/code-standards.md:76`):**

- [ ] `test_api_unknown_product_id_returns_404` — `GET /products/does-not-exist`
      → **404** và thân có khoá `detail`. OBSERVED: `{"detail": "..."}`.
- [ ] `test_api_known_product_id_returns_it` — `GET /products/printer-318469` →
      **200**, `body["id"] == "printer-318469"`. Chọn đúng sản phẩm giá null:
      lấy-theo-id **không** bị luật lọc giá đụng tới.

**Category / brand qua HTTP (quyết định #10, #11):**

- [ ] `test_api_category_accepts_slug_and_folded_name` — `?category=refrigerator`,
      `?category=tu lanh`, `?category=Tủ Lạnh`, `?category=TU LANH` → cả bốn
      `total == 4` và **cùng** tập id. OBSERVED.
- [ ] `test_api_unknown_category_returns_empty` — `?category=bogus` →
      `total == 0` (không phải 20).
- [ ] `test_api_brand_case_insensitive` — `?brand=OPPO` và `?brand=oppo` → cả
      hai `total == 2`. OBSERVED.

**X1 — chuỗi rỗng qua HTTP (quyết định #15, RK16):**

- [ ] `test_api_empty_category_returns_zero_not_all` — `?category=` →
      `total == 0`, **không phải 20**.

      **Đây là silent-200 thứ tư.** OBSERVED qua probe FastAPI thật:
      `?category=` bind thành `""` (không phải `None`), `truthy=False`. Bản dùng
      `if category:` **bỏ qua bộ lọc** và trả **20**; bản dùng
      `if category is not None:` trả **0**. **Cả hai bản pass 100% các test còn
      lại** — `test_api_unknown_category_returns_empty` không bắt được vì
      `"bogus"` truthy (VL-12).
- [ ] `test_api_empty_brand_returns_zero_not_all` — `?brand=` → `total == 0`.

**OpenAPI:**

- [ ] `test_api_openapi_and_docs_served` — `/openapi.json` → **200**,
      `/docs` → **200**. OBSERVED.
- [ ] `test_api_openapi_lists_four_paths` — assert `paths` của
      `/openapi.json` chứa **đúng 4** khoá: `/health`, `/products`,
      `/products/search`, `/products/{product_id}`.

### Implement

Các bước 2-4 ở `## Implementation Steps`.

### Tests After

- [ ] `test_api_sorted_by_id_ascending` — `GET /products?page_size=100` → id
      đầu `air-conditioner-335837`, id cuối `tablet-345544`. OBSERVED.
- [ ] `test_api_dataset_file_unchanged` — ghi lại `mtime` + `st_size` +
      **SHA-256** của `data/products.json` trước, chạy loạt request (list,
      search, filter, by-id, 404), assert **cả ba** không đổi. Chân của A12 ở
      tầng runtime.

      SHA-256 là phần bắt buộc thêm: `mtime`+`size` một mình có thể trùng khớp
      sau một lần ghi-đè cùng độ dài trong cùng giây. Đây là guard **thật** thay
      cho A12 regex cũ — regex đó khớp **0/3** dạng ghi thực tế (VL-16).

### Regression Gate

```
.venv/bin/python -m pytest tests/ -q
```

**MUST PASS 100%.** Commit `feat: add FastAPI routes with ordered path matching`.

## Success

- [ ] `.venv/bin/python -m pytest tests/api/ -q` → **≥ 32 passed, 0 failed,
      0 skipped**.
- [ ] `.venv/bin/python -m pytest tests/ -q` → **0 failed** trên toàn bộ suite.
- [ ] `GET /health` → `products_loaded == **20**` (không phải 90 — canary
      lifespan + quyết định #17).
- [ ] `GET /products/search?q=ngan da` → `total == 3`, đúng 3 id.
- [ ] `GET /products/search?q=may in` → `total == 4`, đúng 4 id.
- [ ] `GET /products?page_size=10` → `total == 20`, `len(items) == 10`.
- [ ] `GET /products?page_size=100` → có `printer-318469`;
      `&min_price=0` → `total == 19`, không có nó.
- [ ] `GET /products?page=3&page_size=10` → **200**, `items == []`, `total == 20`.
- [ ] Sáu trường hợp 422: `q=""`, `q="  "`, `q` thiếu, `page=0`,
      `page_size=101`, `min_price>max_price` — **6/6** trả 422.
- [ ] `?category=` → `total == 0` (**không phải 20**); `?brand=` → `total == 0`.
- [ ] `?q=---` → **200** với `total == 0` (**không** 422).
- [ ] `GET /products/does-not-exist` → **404** + khoá `detail`.
- [ ] `?bogus=x` → **200**.
- [ ] `"specifications" not in items[0]`; `set(items[0])` có **đúng 13** khoá.
- [ ] `/openapi.json` `paths` có **đúng 4** khoá.
- [ ] **Thứ tự route kiểm bằng decorator, không phải chuỗi tự do:**

      ```
      grep -n '@app.get("/products/search")' app/main.py
      grep -n '@app.get("/products/{product_id}")' app/main.py
      ```

      Dòng thứ nhất phải **nhỏ hơn** dòng thứ hai.

      Bản kế hoạch trước grep chuỗi trần `"products/search"` — **hỏng**: chính
      Requirements #1 của phase này bắt đặt **comment cảnh báo** chứa cụm đó
      ngay trên route, nên grep khớp comment và **pass kể cả khi route bị đảo**.
      Grep decorator đầy đủ thì không có chỗ cho comment lọt vào (VL-24).
- [ ] `grep -c "with TestClient" tests/api/conftest.py` → **≥ 1**;
      `grep -c "TestClient(app)" tests/api/test_routes.py` → **0** (không khởi
      tạo trực tiếp trong test).
- [ ] `grep -c "response_model=" app/main.py` → **4** (cả 4 route đều ghim).
- [ ] SHA-256 của `data/products.json` không đổi sau toàn bộ `tests/api/`.
- [ ] `grep -rnE "fastapi|starlette" app/` chỉ khớp `app/main.py` (A11).

## Risks

| Rủi ro | Khả năng | Tác động | Xử lý |
|---|---|---|---|
| Route `{product_id}` trước `search` (RK2) | **H** | **C** — trả **200** với thân sai; assert status-only **PASS trong khi API hỏng** | `test_api_search_route_precedence_body_not_status` assert body. Cộng grep **decorator đầy đủ** ở Success (grep chuỗi trần khớp phải comment cảnh báo → pass kể cả khi đảo, VL-24) |
| Fixture quên `with TestClient(...)` (RK3) | **H** | **C** — index rỗng, mọi assert đếm sai, không lỗi nào bắn | `test_api_health_products_loaded_is_20` là canary; grep `TestClient(app)` trong test file phải = 0 |
| **Route quên `response_model=` (RK17)** | **H** | **H** — `ProductOut` viết xong nhưng không nối vào; rò 5 trường gồm `specifications`, payload **2.24x** | Requirements #9 ghim cả 4 route. `test_api_response_does_not_leak_raw_fields` + `grep -c "response_model=" == 4` |
| **Bộ lọc dùng truthiness (RK16)** | **H** | **H** — `?category=` trả **20** thay vì 0; silent-200 thứ tư | `test_api_empty_category_returns_zero_not_all`. Logic `is not None` nằm ở P4 Requirements #4 |
| **`/health` trả `len(index)` = 90 thay vì 20 (VL-13)** | **M** | **M** — test đỏ **sai lý do**, người sửa chữa nhầm chỗ | Requirements #8 ghim `len(dataset.products)` kèm giải thích số học |
| Chỉ dùng `Query(min_length=1)` cho `q` (RK4) | **H** | **H** — `?q=%20%20` trả 200, vi phạm quyết định #4 | `test_api_q_whitespace_only_returns_422` |
| Trộn `q` rời với query model (RK10) | **M** | **H** — **mọi** request 422, kể cả hợp lệ | D-A: kế thừa `SearchParams(ListParams)`. Đã probe xanh |
| Tự chế thân lỗi 422 bằng `HTTPException` | M | M — hai format lỗi song song, vi phạm `docs/code-standards.md:72` | `model_validator` cho 422 native. Test assert 422, không assert chuỗi lỗi tự chế |
| Bật `extra: forbid` "cho chặt" | M | M — mở rộng scope lén, param lạ thành 422 | `test_api_unknown_query_param_ignored` |
| Logic lọc/sort rò rỉ vào `main.py` | M | M — nhân bản với phase 4, khó test | Route chỉ điều phối; review kiểm |
| `lifespan` try/except nuốt lỗi nạp (RK13) | L | **C** — app lên với index rỗng, trả 0 kết quả cho mọi truy vấn | Không try/except; `test_api_lifespan_loaded_index` bắt |
| Route ghi vào `data/products.json` | L | **C** — hỏng nguồn dữ liệu | `test_api_dataset_file_unchanged` (mtime + size) |
