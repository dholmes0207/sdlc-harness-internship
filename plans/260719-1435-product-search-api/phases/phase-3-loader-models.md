---
phase: 3
title: "Loader Models"
status: pending
plan: 260719-1435-product-search-api
created: 2026-07-19
harness_version: 5.3.0
harness_kit_digest: 251ed307796039124b44d71759b3f62d8bb9135c4bf3053156e38798587a50a8
harness_schema_version: 1.0
---

# Phase 3 — Loader Models

## Overview

Hai việc, cùng một tầng dữ liệu:

1. `app/loader.py` — đọc `data/products.json`, **hiểu đúng đó là envelope dict**,
   trả về danh sách sản phẩm + bản đồ category. **Fail fast** khi dữ liệu hỏng.
2. `app/models.py` — Pydantic v2 response models: sản phẩm, pagination envelope,
   lỗi.

Điểm chết người của phase này: **top-level JSON là `dict`, KHÔNG phải `list`.**
OBSERVED trong phiên này —
`keys = [schema_version, source_file, source_note, currency, total, categories, products]`.
Discovery brief **không hề nhắc** chi tiết này. Ai code theo trực giác
`json.load(...)` rồi lặp thẳng sẽ lặp qua **tên các key**, không phải sản phẩm.

Bẫy thứ hai, ghi rõ ở `docs/code-standards.md:18-21`: envelope có sẵn key
`total = 20` **trùng tên** với `total` của pagination envelope (= số bản ghi
khớp sau lọc). Hai khái niệm khác nhau. Loader **bỏ qua** `d["total"]`.

**Phụ thuộc:** phase 1 **và phase 2**. `build_category_map` + `resolve_category`
gọi `app.text.fold`, mà `app/text.py` là tài sản của phase 2 — **không** chạy
phase này trước phase 2. (Bản kế hoạch trước ghi "độc lập với phase 2": **sai**,
sửa ở VL-14. `plan-graph.yaml` giờ có cạnh `{from: P2, to: P3}`.)

## Files

**Create**

- `app/loader.py`
- `app/models.py`
- `tests/unit/test_loader.py`
- `tests/unit/test_models.py`

Không chạm file nào của phase khác.

## Requirements

### `app/loader.py`

1. `load_dataset(path: Path | None = None) -> Dataset` — đọc file, validate, trả
   về một dataclass/NamedTuple `Dataset` gồm:
   - `products: list[dict]` — từ `d["products"]`
   - `categories: list[dict]` — từ `d["categories"]`

   **Chữ ký chốt: `path` có default, KHÔNG bắt buộc.** Default phải là đường dẫn
   **tuyệt đối dẫn xuất từ `__file__`**, không phải chuỗi tương đối:

   ```python
   DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "products.json"
   ```

   Bản kế hoạch trước ghi `load_dataset(path: Path)` ở đây nhưng phase 5 gọi
   `load_dataset()` không tham số — **mâu thuẫn chữ ký** (VL-19). Và một default
   tương đối kiểu `"data/products.json"` sẽ **hỏng với bất kỳ cwd nào khác repo
   root** — chạy `uvicorn` từ thư mục khác, hoặc `WORKDIR` khác trong container,
   là đủ để loader fail-fast dù dữ liệu vẫn nằm đúng chỗ.
   `Path(__file__)` neo vào vị trí module, không phụ thuộc cwd.
2. **Fail fast** (`docs/code-standards.md:78-79`) — raise khi:
   - file không tồn tại
   - JSON không parse được
   - top-level không phải `dict`
   - thiếu key `products` hoặc `categories`
   - `products` rỗng
   - có sản phẩm thiếu key `id`, hoặc `id` **trùng nhau**

   Raise một exception riêng `DatasetError` để phase 5 phân biệt được với lỗi
   khác. **Cấm** `except: pass` (`docs/code-standards.md:16`).

   Lý do fail fast: một API khởi động được nhưng trả 0 kết quả cho mọi truy vấn
   là chế độ hỏng **tệ hơn crash** (RK13).

3. `build_category_map(categories: list[dict]) -> dict[str, str]` — bản đồ
   **dual-key** cho quyết định #10. Với mỗi entry: nạp cả `slug` **và**
   `fold(name)` → cùng trỏ tới `slug`.

   OBSERVED, chạy thật trên dữ liệu thật, map có **đúng 10 key** (tất cả đều là
   key **đã fold**):
   ```
   air-conditioner, man hinh may tinh, may in, may lanh, may tinh bang,
   monitor, printer, refrigerator, tablet, tu lanh
   ```

   Hàm này **gọi** `app.text.fold` — không cài lại (`docs/code-standards.md:42`).

3b. **`resolve_category(category_map: dict[str, str], value: str) -> str | None`
    — hàm SỞ HỮU việc fold ĐẦU VÀO.** Trả `category_map.get(fold(value))`.

    **Vì sao cần một hàm riêng, không tra thẳng `category_map.get(value)`:** key
    trong map **đã được fold**, nhưng giá trị người dùng gửi lên thì **chưa**.
    Bản kế hoạch trước không nói ai chịu trách nhiệm fold đầu vào, nên tra thẳng
    sẽ hỏng **3/7** case đã liệt kê (VL-18):

    | Đầu vào | `category_map.get(value)` thô | `resolve_category(...)` |
    |---|---|---|
    | `refrigerator` | `refrigerator` ✓ | `refrigerator` ✓ |
    | `tu lanh` | `refrigerator` ✓ | `refrigerator` ✓ |
    | `Tủ Lạnh` | **`None`** ✗ | `refrigerator` ✓ |
    | `TU LANH` | **`None`** ✗ | `refrigerator` ✓ |
    | `may in` | `printer` ✓ | `printer` ✓ |
    | `Máy in` | **`None`** ✗ | `printer` ✓ |
    | `monitor` | `monitor` ✓ | `monitor` ✓ |
    | `bogus` | `None` ✓ | `None` ✓ |

    `app/search.py` (phase 4) gọi **hàm này**, không bao giờ tra map trực tiếp.

4. `effective_price(product: dict) -> float | None` — thuật ngữ đã đăng ký
   (`docs/glossary.yaml`): trả `effective_price`, fallback `original_price`,
   `None` khi cả hai null. **CẤM** đặt tên `final_price` / `real_price`.

   OBSERVED: **đúng 1** sản phẩm trả `None` — `printer-318469`.

**Chỉ đọc.** Loader **không bao giờ** mở file ở chế độ ghi (A12).

### `app/models.py`

5. `ProductOut` — Pydantic model cho một sản phẩm trong response. Trường:
   `id, sku, name, slug, category, category_name, brand, original_price,
   sale_price, effective_price, discount_percent, currency, promotion`.

   **Loại khỏi response:** `availability` (OBSERVED `"unknown"` 20/20 — vô
   nghĩa), `image_url` (OBSERVED null 20/20), `specifications` (43 key rời rạc,
   ngoài scope), `product_id_web`, `model_code`.

6. `PaginationEnvelope` — thuật ngữ đã đăng ký. Trường **đúng 4**:
   `total: int`, `page: int`, `page_size: int`, `items: list[ProductOut]`.
   **CẤM** đặt tên `wrapper` / `result_set` (`docs/glossary.yaml`).

   Docstring phải ghi: `total` = số bản ghi khớp sau lọc, **trước khi cắt
   trang** — khác `data["total"]` của dataset.

7. `HealthOut` — `{"status": str, "products_loaded": int}`. Xem câu hỏi mở #2
   ở plan.md; nếu người dùng chốt khác thì sửa ở đây.

8. Pydantic **v2** API — `model_validate`, `model_dump`. **Cấm** API v1
   (`docs/code-standards.md:11-12`).

## Implementation Steps

1. Viết `tests/unit/test_loader.py` + `tests/unit/test_models.py` → chạy →
   **đỏ** (`ModuleNotFoundError`).
2. `app/models.py`: `ProductOut`, `PaginationEnvelope`, `HealthOut`.
3. `app/loader.py`: `DatasetError`, `Dataset`, `DEFAULT_DATA_PATH`,
   `load_dataset`, `build_category_map`, **`resolve_category`**,
   `effective_price`.
4. `build_category_map` + `resolve_category` import `fold` từ `app.text` —
   **không** cài lại.
5. Chạy lại → xanh.

## TDD

### Tests-before (RED)

**Loader — hình dạng envelope (RK9):**

- [ ] `test_load_dataset_reads_products_key` — nạp file thật, assert trả **đúng
      20** sản phẩm. Khoá: đọc `d["products"]`, không lặp qua key của dict.
- [ ] `test_load_dataset_reads_categories_key` — assert **đúng 5** category.
- [ ] `test_loader_ignores_dataset_total_key` — assert đối tượng `Dataset` trả
      về **không có** thuộc tính/khoá tên `total`. Khoá RK8 ngay từ tầng loader:
      không cho `d["total"]=20` rò rỉ lên response.
- [ ] `test_product_ids_are_unique` — `len({p["id"]})== 20`. **invariant**.

**Loader — fail fast (RK13):**

- [ ] `test_load_dataset_raises_on_missing_file` → `DatasetError`.
- [ ] `test_load_dataset_raises_on_invalid_json` → `DatasetError` (tmp file rác).
- [ ] `test_load_dataset_raises_on_toplevel_list` — đưa vào `[]` (list, không
      phải dict) → `DatasetError`. **Khoá trực tiếp RK9.**
- [ ] `test_load_dataset_raises_on_missing_products_key` → `DatasetError`.
- [ ] `test_load_dataset_raises_on_empty_products` — `{"products": [], ...}` →
      `DatasetError`. Khoá: không cho khởi động với index rỗng.
- [ ] `test_load_dataset_raises_on_duplicate_ids` → `DatasetError`.

**Category map + resolve (quyết định #10):**

- [ ] `test_category_map_has_10_keys` — assert **đúng 10** key, so khớp danh
      sách chính xác ở Requirements #3.
- [ ] `test_resolve_category_accepts_slug_and_display_name` — bảng tham số, gọi
      qua **`resolve_category`** (không tra map trực tiếp):
      `refrigerator`→`refrigerator`, `tu lanh`→`refrigerator`,
      `Tủ Lạnh`→`refrigerator`, `TU LANH`→`refrigerator`, `may in`→`printer`,
      `Máy in`→`printer`, `monitor`→`monitor`.
      **3/7 case này (`Tủ Lạnh`, `TU LANH`, `Máy in`) KHÔNG thể pass nếu tra
      thẳng `category_map.get(value)`** — đó chính là lý do `resolve_category`
      tồn tại (VL-18).
- [ ] `test_resolve_category_returns_none_for_unknown` — `bogus` → `None`.
- [ ] `test_resolve_category_returns_none_for_empty_string` — `""` → `None`,
      và `"   "` → `None`. Khoá quyết định #15 ở tầng thuần túy: chuỗi rỗng
      **không** được coi là "không lọc".
- [ ] `test_loader_uses_text_fold_not_local_copy` — assert
      `app.loader.fold is app.text.fold` (**identity đối tượng hàm**, không phải
      grep). Khoá `docs/code-standards.md:42` — đây là guard thật thay cho A10
      grep cũ, thứ bị `str.maketrans` lách qua với **0** hit (VL-16).

**Effective price (glossary term):**

- [ ] `test_effective_price_prefers_effective_over_original`.
- [ ] `test_effective_price_falls_back_to_original` — khi `effective_price`
      null nhưng `original_price` có.

      **Test này BẮT BUỘC dùng dict tổng hợp, không phải dữ liệu thật.**
      OBSERVED: trong `data/products.json` có **0** sản phẩm ở trạng thái
      (`effective_price` null **và** `original_price` có) — 19 sản phẩm có
      `effective_price`, 1 sản phẩm (`printer-318469`) null cả hai. Nhánh
      fallback là **nhánh chết trên dữ liệu thật**; chỉ dict tự dựng mới chạm
      tới nó (VL-23). Ghi rõ điều này trong docstring của test
      để người sau không đi tìm sản phẩm thật rồi tưởng mình đọc sai dataset.
- [ ] `test_effective_price_none_for_printer_318469` — assert **đúng 1** sản
      phẩm trong toàn dataset trả `None`, và `id` của nó là **`printer-318469`**.
      Đây là chân R4 ở tầng loader.

**Models:**

- [ ] `test_pagination_envelope_has_exactly_4_fields` — assert
      `set(PaginationEnvelope.model_fields) == {"total","page","page_size","items"}`.
      **invariant** khoá hình dạng hợp đồng.
- [ ] `test_product_out_excludes_dead_fields` — assert `availability`,
      `image_url`, `specifications` **không** nằm trong `model_fields`.
- [ ] `test_product_out_validates_real_product` — `ProductOut.model_validate`
      chạy được trên **cả 20** sản phẩm thật, không ném lỗi. Khoá: model khớp
      dữ liệu thật, kể cả `printer-318469` với giá null.

### Implement

Các bước 2-4 ở `## Implementation Steps`.

### Tests After

- [ ] `test_load_dataset_default_path_works_from_any_cwd` — `os.chdir(tmp_path)`
      rồi gọi `load_dataset()` **không tham số** → vẫn trả **đúng 20** sản phẩm.
      Khoá `DEFAULT_DATA_PATH` neo vào `__file__`, không vào cwd (VL-19). Một
      default tương đối sẽ làm test này đỏ.
- [ ] `test_loader_never_writes_dataset` — ghi `mtime` + `st_size` + **SHA-256**
      của `data/products.json` trước, gọi `load_dataset()` nhiều lần, assert cả
      ba **không đổi**. Chân của A12 ở tầng loader.

      Bản kế hoạch trước dùng grep source tìm `open(..., "w")` — **đã đo, guard
      đó vô dụng**: nó khớp **0/3** dạng ghi thực tế (`p.write_text(...)`,
      `open(DATA_PATH,"w")` với biến, `open(path,"w").write(x)`). So SHA-256 bắt
      **mọi** dạng ghi, bất kể viết bằng API nào (VL-16).

### Regression Gate

```
.venv/bin/python -m pytest tests/ -q
```

**MUST PASS 100%.** Commit `feat: add dataset loader and response models`.

## Success

- [ ] `load_dataset()` — **không tham số** — trả **đúng 20** sản phẩm và **đúng
      5** category từ file thật, **kể cả khi cwd ≠ repo root**.
- [ ] `build_category_map()` trả **đúng 10** key.
- [ ] `resolve_category()` đúng **7/7** case ở bảng Requirements #3b, trong đó
      **3** case (`Tủ Lạnh`, `TU LANH`, `Máy in`) là những case mà tra map trực
      tiếp sẽ trả `None`.
- [ ] `resolve_category(map, "")` → `None` và `resolve_category(map, "   ")` →
      `None` (quyết định #15).
- [ ] `effective_price` trả `None` cho **đúng 1/20** sản phẩm, `id` =
      `printer-318469`.
- [ ] 6 trường hợp dữ liệu hỏng đều raise `DatasetError` — **6/6**, không cái
      nào trả về im lặng.
- [ ] `set(PaginationEnvelope.model_fields)` == **đúng** 4 tên trường đã chốt.
- [ ] `ProductOut.model_validate` chạy sạch trên **20/20** sản phẩm.
- [ ] SHA-256 của `data/products.json` **không đổi** sau khi chạy toàn bộ
      `tests/unit/test_loader.py`.
- [ ] `.venv/bin/python -m pytest tests/unit/test_loader.py tests/unit/test_models.py -q`
      → **≥ 24 passed, 0 failed**.
- [ ] `app.loader.fold is app.text.fold` → `True` (guard thật thay grep).
- [ ] `grep -rnE "fastapi|starlette" app/loader.py app/models.py` → **0 dòng**
      cho `loader.py`. `models.py` chỉ import `pydantic`, không `fastapi` (A11).
- [ ] `grep -rnE "final_price|real_price|wrapper|result_set" app/` → **0 dòng**
      (từ cấm trong `docs/glossary.yaml`).

## Risks

| Rủi ro | Khả năng | Tác động | Xử lý |
|---|---|---|---|
| Coi top-level là list (RK9) | **M** | **H** — `TypeError` lúc khởi động, hoặc index rỗng | `test_load_dataset_raises_on_toplevel_list` + `test_load_dataset_reads_products_key` |
| Dùng `d["total"]` làm `total` của envelope (RK8) | **M** | **M** — đúng tình cờ khi không lọc, sai ngay khi lọc | `test_loader_ignores_dataset_total_key`; `Dataset` không mang trường `total` |
| Nuốt lỗi nạp dữ liệu, khởi động với index rỗng (RK13) | L | **C** — API trả 0 kết quả cho mọi truy vấn | 6 test fail-fast; cấm `except: pass` |
| Nhân bản logic fold vào `loader.py` | M | M — review chặn merge | `test_loader_uses_text_fold_not_local_copy` assert **cùng một object hàm** (`is`), không grep — grep `unicodedata` bị `str.maketrans` lách |
| **Không ai fold đầu vào `category` (RK18)** | **H** | **M** — 3/7 case đã liệt kê không thể pass | `resolve_category()` sở hữu việc fold đầu vào; `test_resolve_category_accepts_slug_and_display_name` phủ cả 7 case |
| **Chữ ký `load_dataset` lệch giữa P3 và P5, hoặc default tương đối** | **M** | **H** — loader fail-fast khi cwd ≠ repo root, dù dữ liệu vẫn đúng chỗ | `path: Path \| None = None` + `DEFAULT_DATA_PATH` neo `__file__`; `test_load_dataset_default_path_works_from_any_cwd` chạy sau `os.chdir` |
| Test fallback giá tưởng chạm được nhánh thật | M | L — người sau tưởng đọc sai dataset | OBSERVED **0** sản phẩm ở trạng thái đó; test dùng dict tổng hợp, ghi lý do trong docstring (VL-23) |
| Đặt tên vi phạm glossary (`final_price`, `wrapper`) | M | L | Success criteria có grep từ cấm |
| `ProductOut` bắt buộc trường mà `printer-318469` để null | M | M — validate chết trên đúng sản phẩm quan trọng nhất | `test_product_out_validates_real_product` chạy cả 20; giá phải là `float \| None` |
| Đưa `specifications` vào response "cho đủ" | L | M — mở rộng scope lén, 43 key rời rạc | `test_product_out_excludes_dead_fields` |
