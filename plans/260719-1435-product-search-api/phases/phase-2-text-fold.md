---
phase: 2
title: "Text Fold"
status: pending
plan: 260719-1435-product-search-api
created: 2026-07-19
harness_version: 5.3.0
harness_kit_digest: 251ed307796039124b44d71759b3f62d8bb9135c4bf3053156e38798587a50a8
harness_schema_version: 1.0
---

# Phase 2 — Text Fold

## Overview

Viết `app/text.py`: **accent fold** đ-aware + tokenizer. Thuần túy, không biết
gì về HTTP, không biết gì về sản phẩm — chỉ `str` vào, `str`/`set[str]` ra.

Đây là **phase rủi ro nhất của cả plan**. `DEC-1` và
`docs/system-architecture.md:73-75` đều chỉ vào đúng một sự thật đo được:
`unicodedata` NFD **không đụng tới `đ`** (nó không thuộc category `Mn`), nên
`"Ngăn đá"` fold ra `"ngan đa"`, người dùng gõ `"ngan da"` nhận **0 kết quả mà
không có lỗi nào bắn ra**. Re-verified trong phiên này: NFD ngây thơ để sót `đ`
ở **8/20** blob; sau khi map `đ→d` riêng: **0/20**.

`docs/code-standards.md:42` — logic fold sống ở **đúng một chỗ**, chính là file
này. Nhân bản nó ra chỗ khác là lỗi review chặn merge.

**Phụ thuộc:** phase 1.

**Quan hệ với phase 3 là MỘT CHIỀU, không phải độc lập hai chiều.** P2 **không**
phụ thuộc P3 — `app/text.py` không import gì từ `app/loader.py`. Nhưng **P3 phụ
thuộc P2**: `build_category_map` và `resolve_category` ở `app/loader.py` gọi
`app.text.fold`. Nên thứ tự **P2 → P3** là bắt buộc, và `plan-graph.yaml` có cạnh
`{from: P2, to: P3}` (red-team H4 / VL-14). Viết "độc lập với phase 3" là **sai**.

## Files

**Create**

- `app/text.py`
- `tests/unit/test_text.py`

Không chạm file nào của phase khác.

## Requirements

**Functional**

1. `fold(s: str) -> str` — chuẩn hoá chuỗi tiếng Việt về ASCII thường:
   1. `unicodedata.normalize("NFD", s)`
   2. bỏ mọi ký tự category `Mn`
   3. **rồi** map `đ→d`, `Đ→d`
   4. `.lower()`

   Thứ tự bước 3 **sau** bước 2 là load-bearing. Đảo lại vẫn chạy nhưng khiến
   quan hệ nhân quả khó đọc; giữ đúng thứ tự đã ghi ở
   `docs/system-architecture.md:73`.

2. `tokenize(s: str) -> set[str]` — fold rồi tách token theo
   `re.findall(r"[a-z0-9]+", ...)`. Trả `set`, không phải `list` — thứ tự token
   không mang nghĩa, và bước khớp ở phase 4 là phép so tập hợp.

**Non-functional**

3. **Cấm** `import fastapi` / `starlette` trong file này
   (`docs/code-standards.md:14-15`).
4. Type hint bắt buộc trên cả hai hàm (`docs/code-standards.md:11`).
5. Tên định danh tiếng Anh, docstring tiếng Việt (`docs/code-standards.md:13`).
6. Dùng thuật ngữ **`accent fold`**. **CẤM** đặt tên `normalize` hoặc `slugify`
   (`docs/glossary.yaml` term `accent fold`, `forbidden: [normalize, slugify]`).
   Lưu ý: `unicodedata.normalize` là API stdlib, gọi nó thì được — cấm là cấm
   đặt **tên hàm của mình** là `normalize`.

## Implementation Steps

1. Viết `tests/unit/test_text.py` với toàn bộ test ở mục TDD → chạy → **đỏ**
   (`ModuleNotFoundError: No module named 'app.text'`).
2. Tạo `app/text.py`, `import re`, `import unicodedata`.
3. Định nghĩa hằng module `_D_STROKE_MAP = {"đ": "d", "Đ": "d"}` kèm comment
   tiếng Việt giải thích **vì sao** cần: NFD không xử lý `đ`, hỏng 8/20.
4. Cài `fold()` theo đúng 4 bước ở Requirements #1.
5. Cài `tokenize()` = `set(re.findall(r"[a-z0-9]+", fold(s)))`.
6. Chạy lại → xanh.

## TDD

### Tests-before (RED)

**R1 — test hồi quy bắt buộc #1 của `docs/code-standards.md:53-55`. Test quan
trọng nhất bộ test.**

- [ ] `test_fold_maps_d_stroke_to_d` — `fold("Ngăn đá") == "ngan da"`.
      Khoá: đúng cái NFD làm hỏng. Đây là R1 tầng unit.
- [ ] `test_fold_d_stroke_uppercase` — `fold("Đá") == "da"`. Khoá nhánh `Đ` hoa;
      map một chiều `đ` mà quên `Đ` là lỗi nửa vời hay gặp.
- [ ] `test_naive_nfd_would_fail_this` — assert `"đ" not in fold("Ngăn đá")`.
      Khoá **trực tiếp** chế độ hỏng, không gián tiếp: nếu ai đó thay thân hàm
      bằng NFD trần, test này đỏ với thông điệp rõ ràng.
- [ ] `test_fold_strips_all_vietnamese_diacritics` — bảng tham số:
      `"Tủ Lạnh"→"tu lanh"`, `"Máy lạnh"→"may lanh"`,
      `"Màn hình máy tính"→"man hinh may tinh"`, `"Máy tính bảng"→"may tinh bang"`,
      `"Máy in"→"may in"`. Năm `category_name` thật, OBSERVED từ
      `data/products.json`.
- [ ] `test_fold_is_idempotent` — `fold(fold(s)) == fold(s)` trên cả 5 chuỗi trên.
      Đây là **invariant**, không phải cặp input/output đơn lẻ.
- [ ] `test_fold_lowercases` — `fold("SAMSUNG") == "samsung"`,
      `fold("Hp") == "hp"`, `fold("OPPO") == "oppo"`. Khoá brand khớp không phân
      biệt hoa thường (quyết định #11). OBSERVED: brand trong dataset lẫn lộn
      casing (`Hp`, `Aoc`, `OPPO`).
- [ ] `test_tokenize_splits_on_non_alnum` —
      `tokenize("Tủ Lạnh 4 Cửa - 508L") == {"tu","lanh","4","cua","508l"}`.
      Khoá: giữ số, cắt dấu gạch/khoảng trắng.
- [ ] `test_tokenize_returns_set_not_list` — `isinstance(tokenize("a b"), set)`.
      Khoá kiểu trả về mà phase 4 phụ thuộc.
- [ ] `test_tokenize_empty_and_whitespace` — `tokenize("") == set()`,
      `tokenize("   ") == set()`. Khoá biên; phase 5 dựa vào đây để quyết `q`
      toàn khoảng trắng.
- [ ] `test_tu_tu_homograph_collapses` — `fold("tủ") == fold("từ") == "tu"`.
      **Khoá trade-off có chủ ý**, không phải bug (RK11). Ai đó "sửa" cho
      `"tủ"≠"từ"` sẽ làm đỏ test này và phải đọc lý do.

### Implement

Các bước 2-5 ở `## Implementation Steps`.

### Tests After

- [ ] Test dựa trên dataset thật: nạp `data/products.json`, fold blob
      `name + " " + brand + " " + category_name` của cả 20 sản phẩm, assert
      **0/20** blob còn chứa `"đ"` hoặc `"Đ"`.
      Tên: `test_no_d_stroke_survives_fold_across_dataset`.
      OBSERVED: naive NFD → **8/20** sót; đ-aware → **0/20**.

### Regression Gate

```
.venv/bin/python -m pytest tests/ -q
```

**MUST PASS 100%.** Commit `feat: add d-aware accent fold and tokenizer`.

## Success

- [ ] `fold("Ngăn đá")` trả **đúng** `"ngan da"`.
- [ ] Fold blob toàn dataset: **0/20** còn ký tự `đ`/`Đ` (bench naive NFD: 8/20).
- [ ] `tokenize("Tủ Lạnh 4 Cửa - 508L")` trả **đúng 5** token:
      `{"tu","lanh","4","cua","508l"}`.
- [ ] `.venv/bin/python -m pytest tests/unit/test_text.py -q` → **≥ 10 passed,
      0 failed**.
- [ ] `grep -rl "unicodedata" app/` → **đúng 1 dòng**, là `app/text.py` (A10).
- [ ] `grep -rnE "fastapi|starlette" app/text.py` → **0 dòng** (A11).
- [ ] `grep -rnE "def (normalize|slugify)" app/text.py` → **0 dòng** (từ cấm).

## Risks

| Rủi ro | Khả năng | Tác động | Xử lý |
|---|---|---|---|
| Cài NFD trần, quên map `đ` (RK1) | **H** | **C** — hỏng thầm lặng 8/20, không lỗi nào bắn | 3 test riêng khoá đúng chỗ này (`test_fold_maps_d_stroke_to_d`, `test_naive_nfd_would_fail_this`, `test_no_d_stroke_survives_fold_across_dataset`) |
| Map `đ` **trước** NFD | M | L — kết quả vẫn đúng, nhưng lệch tài liệu kiến trúc | Comment tại chỗ + `docs/system-architecture.md:73` ghi rõ thứ tự |
| Quên nhánh `Đ` hoa | M | M — hỏng một nửa số trường hợp | `test_fold_d_stroke_uppercase` |
| Regex tokenizer nuốt mất chữ số | M | M — `"508L"` mất, model code không tìm được | `test_tokenize_splits_on_non_alnum` assert `"508l"` có mặt |
| Ai đó nhân bản logic fold sang `index.py`/`search.py` | M | M — review chặn merge (`docs/code-standards.md:102`) | A10: `grep -rl "unicodedata" app/` phải trả đúng 1 |
| "Sửa" va chạm `tủ`/`từ` cho precision đẹp hơn | L | M — phá trade-off đã chốt, ngoài scope | `test_tu_tu_homograph_collapses` khoá hành vi lại |
