---
phase: 4
title: "Search Core"
status: pending
plan: 260719-1435-product-search-api
created: 2026-07-19
harness_version: 5.3.0
harness_kit_digest: 251ed307796039124b44d71759b3f62d8bb9135c4bf3053156e38798587a50a8
harness_schema_version: 1.0
---

# Phase 4 — Search Core

## Overview

Trái tim của hệ thống, và là phase **thuần túy** cuối cùng — không một dòng nào
biết HTTP tồn tại.

1. `app/index.py` — dựng **token index** `dict[token -> set[product_id]]` từ
   `name` + `brand` + `category_name` đã fold.
2. `app/search.py` — khớp AND, lọc category/brand/giá, sort `id` tăng dần, đếm
   `total` **trước** khi cắt trang, cắt trang.

Ba trong bốn test hồi quy bắt buộc của `docs/code-standards.md:51-65` có chân ở
đây (R1, R2, R4) cộng R5. Vì tầng này thuần túy, test chạy nhanh và assert được
vào **id cụ thể**, không phải "không ném lỗi".

**Phụ thuộc:** phase 2 (`fold`, `tokenize`) và phase 3 (`load_dataset`,
`build_category_map`, `effective_price`).

## Files

**Create**

- `app/index.py`
- `app/search.py`
- `tests/unit/test_index.py`
- `tests/unit/test_search.py`

Không chạm file nào của phase khác.

## Requirements

### `app/index.py`

1. `build_token_index(products: list[dict]) -> dict[str, set[str]]` — với mỗi
   sản phẩm, dựng blob `name + " " + brand + " " + category_name`, `tokenize`,
   rồi map mỗi token → `set` chứa `product["id"]`.

   **Chỉ ba trường này** (quyết định #2). **Không** `specifications`, không
   `slug`, không `model_code`, không `sku`.

   Hệ quả đã biết và chấp nhận: `"dien"` trả **0 kết quả** dù `"điện"` có trong
   `specifications` (OBSERVED).

2. `lookup(index, query: str) -> set[str]` — `tokenize` query, rồi **giao**
   (`set.intersection`) các posting set. Đây là phép **AND** ở quyết định #3.
   Query không có token nào → trả `set()`.

   **Khớp token NGUYÊN VẸN**, không prefix, không substring (`DEC-1`). Vì index
   khoá theo token đầy đủ, tra cứu dict tự nhiên cho đúng ngữ nghĩa này — nhưng
   test vẫn phải khoá lại, vì "tối ưu" thành prefix scan là refactor rất dễ xảy ra.

3. Dùng thuật ngữ **`token index`**. **CẤM** `search index` / `inverted index`
   (`docs/glossary.yaml`).

### `app/search.py`

4. `run(dataset, index, category_map, *, q=None, category=None, brand=None,
   min_price=None, max_price=None, page=1, page_size=10) -> tuple[int, list[dict]]`
   — trả `(total, items)`.

   Thứ tự xử lý **bắt buộc**, đúng như `docs/system-architecture.md:35-37`:

   1. `q` **is not None** → `lookup` → thu hẹp về set id. `q is None` → toàn bộ 20.
   2. Lọc `category`: **`if category is not None:`** → `resolve_category(...)`.
      Resolve ra `None` (không nhận ra, **hoặc chuỗi rỗng**) → **0 kết quả**,
      không phải bỏ qua bộ lọc.
   3. Lọc `brand`: **`if brand is not None:`** → so
      `fold(p["brand"]) == fold(brand)` (quyết định #11). Brand không khớp ai →
      **0 kết quả**.
   4. Lọc giá: chỉ khi `min_price` **hoặc** `max_price` **is not None**. Dùng
      `effective_price(p)`; **`None` → loại** (quyết định #5). Không có bộ lọc
      giá → sản phẩm giá null **vẫn hiện**. Biên **inclusive**:
      `eff >= min_price` và `eff <= max_price` (quyết định #16).
   5. Sort theo `id` **tăng dần** (quyết định #7).
   6. `total = len(kết quả)` — **ĐẾM Ở ĐÂY**, trước bước 7.
   7. Cắt `[(page-1)*page_size : page*page_size]`.

   **Bước 2-4 PHẢI dùng `is not None`, TUYỆT ĐỐI không dùng truthiness**
   (quyết định #15, RK16). Đây là chế độ hỏng thầm-lặng-200 **thứ tư**, và nó
   nguy hiểm vì **cả hai bản đều pass 100% bộ test cũ**:

   | Đầu vào | `if category:` | `if category is not None:` |
   |---|---|---|
   | `?category=refrigerator` | lọc → **4** | lọc → **4** |
   | `?category=bogus` | lọc → **0** | lọc → **0** |
   | **`?category=`** (rỗng) | **bỏ qua → 20** ✗ | resolve `""`→`None` → **0** ✓ |

   OBSERVED qua probe FastAPI thật: `?category=` bind thành `""` —
   `is_none=False`, `truthy=False`. Test `category=bogus` cũ **đi thẳng qua** lỗ
   này vì `"bogus"` truthy. Ghi chú thêm: `?category=%20` (một dấu cách) bind
   thành `" "`, **truthy** — nên truthiness còn xử lý hai ca rỗng khác nhau,
   `is not None` thì nhất quán.

   Bước 6 trước bước 7 là toàn bộ nội dung của R5. Đảo lại thì `total` biến
   thành `len(items)` và pagination sai âm thầm (RK7).

5. `page` vượt `total` → trả `(total_đúng, [])`, **không** raise, không 404
   (quyết định #13). Phase 5 dịch thẳng thành `items: []`.

6. **Cấm** `import fastapi` / `starlette` (`docs/code-standards.md:14-15`).
   Hàm này nhận tham số Python thường, không nhận `Request`.

7. `search.py` import `fold` từ `app.text` và `resolve_category` /
   `effective_price` từ `app.loader` — **không cài lại cái nào**
   (`docs/code-standards.md:42`). `app.search.fold is app.text.fold` phải đúng.

## Implementation Steps

1. Viết `tests/unit/test_index.py` + `tests/unit/test_search.py` → chạy →
   **đỏ**.
2. `app/index.py`: `build_token_index`, `lookup`.
3. `app/search.py`: `run` theo đúng 7 bước trên. Comment tiếng Việt tại bước 6
   ghi rõ **vì sao** đếm trước khi cắt.
4. Chạy lại → xanh.

## TDD

### Tests-before (RED)

**R1 — fold `đ`, tầng search (`docs/code-standards.md:53-55`):**

- [ ] `test_search_ngan_da_returns_3` — `q="ngan da"` trả **đúng 3** id:
      `refrigerator-358160`, `refrigerator-363108`, `refrigerator-363109`.
      OBSERVED. Index dùng naive NFD cho **0 hit** — test này là cái bắt.

**R2 — token nguyên vẹn (`docs/code-standards.md:56-57`):**

- [ ] `test_search_may_in_returns_exactly_4` — `q="may in"` trả **đúng 4** id:
      `printer-318468`, `printer-318469`, `printer-357980`, `printer-357982`.
      OBSERVED: substring trả **16** (vì `"in"` nằm trong `"Inverter"`),
      whole-token trả **4**. Assert `== 4` và assert đúng tập id — không assert
      `> 0`.
- [ ] `test_search_rejects_substring_semantics` — assert
      `"printer-357980" in lookup(index, "may in")` **và**
      `len(lookup(index, "may in")) == 4`. Chặn mọi biến thể prefix/substring.

**R4 — luật giá null (`docs/code-standards.md:61-62`):**

- [ ] `test_null_price_visible_in_list` — `run()` không lọc giá → `total == 20`
      và `printer-318469` **có** trong kết quả.
- [ ] `test_null_price_excluded_by_min_price` — `run(min_price=0)` →
      `total == 19` và `printer-318469` **không** có. OBSERVED.
- [ ] `test_null_price_excluded_by_max_price` — `run(max_price=999_999_999)` →
      `printer-318469` **không** có. Khoá cả hai nhánh, không chỉ `min_price`.
- [ ] `test_search_may_in_with_price_filter_returns_3` — `q="may in"` +
      `min_price=0` → **đúng 3** (`printer-318468`, `printer-357980`,
      `printer-357982`). OBSERVED. Test giao thoa R2 × R4.

**X2 — biên giá inclusive (quyết định #16, RK19):**

- [ ] `test_min_price_boundary_is_inclusive` — `run(min_price=3290000)` →
      `total == 19`.

      **Đây là test DUY NHẤT phân biệt được `>=` và `>`.** OBSERVED: giá
      `effective price` thấp nhất trong dataset **đúng bằng 3290000**, nên
      `>=` cho **19** còn `>` cho **17**. Test `min_price=0` ở trên cho **19**
      dưới **cả hai** phép so sánh — nó **không** khoá được biên (VL-15).
- [ ] `test_max_price_boundary_is_inclusive` — `run(max_price=3290000)` →
      sản phẩm rẻ nhất **có** trong kết quả (`<=`, không phải `<`).

**R5 — `total` trước khi cắt trang (`docs/code-standards.md:64-65`):**

- [ ] `test_pagination_total_before_slicing` — `page_size=10` trên 20 →
      `total == 20` **và** `len(items) == 10`. Nếu ai đó đếm sau khi cắt,
      `total` thành 10 và test đỏ.
- [ ] `test_page_2_returns_remaining_10` — `page=2, page_size=10` →
      `total == 20`, `len(items) == 10`, và **không** id nào trùng trang 1.
- [ ] `test_page_beyond_total_returns_empty_not_error` — `page=3, page_size=10`
      → `total == 20`, `items == []`, **không raise** (quyết định #13).
- [ ] `test_total_reflects_filter_not_dataset_total` — `category="refrigerator"`
      → `total == 4`, **không phải 20**. Khoá RK8: `d["total"]=20` rò rỉ lên
      thì test này đỏ.

**Sort (quyết định #7):**

- [ ] `test_results_sorted_by_id_ascending` — `run()` → id đầu
      **`air-conditioner-335837`**, id cuối **`tablet-345544`**. OBSERVED.
- [ ] `test_sort_differs_from_file_order` — assert id đầu **khác**
      `refrigerator-358160` (phần tử đầu trong file). OBSERVED: hai thứ tự này
      **không** trùng nhau. Khoá contract độc lập-với-file.

**Lọc category dual-key (quyết định #10):**

- [ ] `test_category_filter_accepts_slug_and_folded_name` — bốn dạng
      `refrigerator` / `tu lanh` / `Tủ Lạnh` / `TU LANH` đều cho `total == 4`
      và **cùng một** tập id.
- [ ] `test_unknown_category_returns_zero_not_all` — `category="bogus"` →
      `total == 0`. Khoá chế độ hỏng "không nhận ra thì bỏ qua bộ lọc" — nó trả
      cả 20 và trông như thành công.
- [ ] `test_search_plus_category_narrows` — `q="tu lanh"` +
      `category="refrigerator"` → `total == 4` (từ 8 xuống 4). OBSERVED.

**X1 — chuỗi rỗng KHÔNG được coi là "không lọc" (quyết định #15, RK16):**

- [ ] `test_empty_category_returns_zero_not_all` — `run(category="")` →
      `total == 0`, **không phải 20**.

      **Test `category="bogus"` ở trên KHÔNG bắt được lỗi này.** `"bogus"` là
      truthy nên cả `if category:` lẫn `if category is not None:` đều áp bộ lọc
      và cùng ra 0. Chuỗi rỗng là falsy — chỉ nó mới tách được hai bản
      implementation (**20** vs **0**). Đây là silent-200 thứ tư (VL-12).
- [ ] `test_whitespace_category_returns_zero` — `run(category="   ")` →
      `total == 0`. Khác ca trên: `"   "` **truthy**, nên nó khoá nhánh
      `resolve_category` fold-rồi-miss thay vì nhánh `is not None`.
- [ ] `test_empty_brand_returns_zero_not_all` — `run(brand="")` → `total == 0`.

**Lọc brand (quyết định #11):**

- [ ] `test_brand_filter_case_insensitive` — `OPPO` / `oppo` / `oPPo` đều cho
      `total == 2`. OBSERVED. Dataset lẫn lộn casing (`Hp`, `Aoc`, `OPPO`).
- [ ] `test_brand_hp_matches_one` — `brand="HP"` → `total == 1` (dataset lưu
      `"Hp"`).

**Trade-off có chủ ý (RK11):**

- [ ] `test_tu_lanh_homograph_returns_8` — `q="tu lanh"` → **đúng 8**: 4
      `refrigerator-*` + 4 `air-conditioner-*`. OBSERVED. **Khoá trade-off**,
      không phải bug. Ai "sửa" precision sẽ phải đọc lý do.
- [ ] `test_dien_returns_zero` — `q="dien"` → `total == 0`, dù `"điện"` có
      trong `specifications`. OBSERVED. Khoá quyết định #2.

**Index:**

- [ ] `test_index_only_covers_three_fields` — chọn một token chỉ xuất hiện
      trong `specifications` (không có trong `name`/`brand`/`category_name`),
      assert nó **không** có trong index.
- [ ] `test_index_and_semantics` — token có thật + token không tồn tại → giao
      ra `set()` (AND, không phải OR).
- [ ] `test_lookup_empty_query_returns_empty_set`.
- [ ] `test_lookup_punctuation_only_returns_empty_set` — `lookup(index, "---")`
      → `set()`. OBSERVED: `tokenize("---")` = `set()`. Chân tầng thuần túy của
      X5; `"---"` vượt qua validator non-blank ở P5 nên hành vi phải được định
      nghĩa, không để ngẫu nhiên.

### Implement

Các bước 2-3 ở `## Implementation Steps`.

### Tests After

- [ ] `test_search_never_mutates_dataset` — chạy `run()` với nhiều tổ hợp tham
      số, sau đó assert `dataset.products` vẫn **đúng 20** phần tử và danh sách
      id không đổi. Khoá: sort/lọc không sửa tại chỗ danh sách gốc.
- [ ] `test_search_uses_text_fold_not_local_copy` — assert
      `app.search.fold is app.text.fold` (**identity đối tượng hàm**). Cùng cặp
      với test bên `loader`; `search.py` cũng cần fold (lọc brand) nên nó là chỗ
      thứ hai logic fold có thể bị nhân bản (A10, VL-16).

### Regression Gate

```
.venv/bin/python -m pytest tests/ -q
```

**MUST PASS 100%.** Commit `feat: add token index and search core`.

## Success

Mọi con số dưới đây là OBSERVED, chạy thật trên `data/products.json`:

- [ ] `q="ngan da"` → **đúng 3** (bench naive NFD: **0**).
- [ ] `q="may in"` → **đúng 4** (bench substring: **16**).
- [ ] `q="tu lanh"` → **đúng 8** (4 tủ lạnh + 4 máy lạnh); + `category=refrigerator`
      → **đúng 4**.
- [ ] `q="dien"` → **đúng 0**.
- [ ] Không lọc → `total == 20`, có `printer-318469`.
- [ ] `min_price=0` → `total == 19`, **không** có `printer-318469`.
- [ ] `q="may in"` + `min_price=0` → **đúng 3**.
- [ ] `page_size=10` → `total == 20`, `len(items) == 10`.
- [ ] `page=3, page_size=10` → `total == 20`, `len(items) == 0`, không raise.
- [ ] `category="refrigerator"` → `total == 4` (**không phải 20**).
- [ ] `category="bogus"` → `total == 0` (**không phải 20**).
- [ ] **`category=""` → `total == 0` (không phải 20)** — X1, ca duy nhất tách
      được `if category:` khỏi `if category is not None:`.
- [ ] **`brand=""` → `total == 0`** (không phải 20).
- [ ] **`min_price=3290000` → `total == 19`** — X2, ca duy nhất tách được `>=`
      khỏi `>` (`>` cho **17**).
- [ ] Sort: id đầu `air-conditioner-335837`, id cuối `tablet-345544`.
- [ ] `brand` `OPPO`/`oppo`/`oPPo` → `total == 2` cả ba.
- [ ] `lookup(index, "---")` → `set()`.
- [ ] `.venv/bin/python -m pytest tests/unit/ -q` → **≥ 52 passed, 0 failed**.
- [ ] `app.search.fold is app.text.fold` → `True` (A10, guard thật).
- [ ] `grep -rnE "fastapi|starlette" app/index.py app/search.py` → **0 dòng** (A11).
- [ ] `grep -rniE "search.index|inverted.index" app/` → **0 dòng** (từ cấm).

## Risks

| Rủi ro | Khả năng | Tác động | Xử lý |
|---|---|---|---|
| "Đơn giản hoá" thành substring (RK6) | **M** | **H** — F1 0.91→0.71, `"may in"` trả 16 | `test_search_may_in_returns_exactly_4` assert `== 4` + đúng tập id |
| Đếm `total` **sau** khi cắt trang (RK7) | **M** | **M** — pagination sai âm thầm | `test_pagination_total_before_slicing` + comment tại bước 6 |
| `d["total"]=20` rò rỉ lên envelope (RK8) | **M** | **M** | `test_total_reflects_filter_not_dataset_total` (`category=refrigerator` → 4) |
| Category không nhận ra → **bỏ qua bộ lọc** thay vì trả rỗng | **M** | **H** — trả cả 20, trông như thành công | `test_unknown_category_returns_zero_not_all` |
| **Bộ lọc dùng truthiness thay `is not None` (RK16)** | **H** | **H** — `?category=` trả **20** thay vì 0; **cả hai** bản pass 100% test cũ | Requirements #4 bước 2-4 ghim `is not None`. `test_empty_category_returns_zero_not_all` + `test_empty_brand_returns_zero_not_all` — ca duy nhất tách được hai bản |
| **Biên giá `>` thay `>=` (RK19)** | **M** | **M** — lệch 2 sản phẩm; test `min_price=0` cũ không phát hiện được | `test_min_price_boundary_is_inclusive` tại đúng giá thấp nhất **3290000**: `>=` → 19, `>` → 17 |
| Nhân bản fold vào `search.py` (lọc brand cần fold) | M | M — review chặn merge | `test_search_uses_text_fold_not_local_copy` assert `is` identity |
| Index thêm `specifications` "cho tìm được nhiều hơn" | M | M — phá quyết định #2, precision tụt | `test_index_only_covers_three_fields` + `test_dien_returns_zero` |
| Nhánh `max_price` quên loại giá null | M | M — R4 chỉ đúng một nửa | Test riêng cho **cả hai** nhánh min và max |
| Dùng OR thay AND khi nhiều token | L | **H** — kết quả nở tung | `test_index_and_semantics` |
| Sort không ổn định / sort theo thứ tự file | L | M — pagination đổi nội dung âm thầm | `test_results_sorted_by_id_ascending` + `test_sort_differs_from_file_order` |
| "Sửa" va chạm `tủ`/`từ` (RK11) | L | M — ngoài scope | `test_tu_lanh_homograph_returns_8` khoá hành vi |
| `run()` sửa tại chỗ list gốc | L | M — request sau thấy dữ liệu bẩn | `test_search_never_mutates_dataset` |
