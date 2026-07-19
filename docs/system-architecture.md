# System Architecture

> Trạng thái: đã triển khai (OBSERVED). Code sống tại `app/` (`main.py`, `models.py`,
> `loader.py`, `text.py`, `index.py`, `search.py`); dataset gốc ở `data/products.json`.
> File này mô tả kiến trúc ĐÃ CHỐT cho Product Search API (xem `docs/decisions.md` DEC-1),
> là đầu vào bắt buộc của `hs:plan` / `hs:cook`. Giữ ngắn và đúng.

## Overview

HTTP API đọc-only, chạy local, phục vụ tìm kiếm 20 sản phẩm điện máy tiếng Việt từ một
dataset JSON tĩnh. Toàn bộ dữ liệu nạp một lần lúc khởi động vào bộ nhớ; runtime không bao
giờ ghi xuống đĩa. Không auth, không database ngoài, không UI, không deploy.

Bài toán lõi không phải là quy mô (20 bản ghi) mà là **chất lượng khớp text tiếng Việt
không dấu** — đây là nơi mọi rủi ro kỹ thuật tập trung.

## Components

| Component | Trách nhiệm | Ghi chú |
|---|---|---|
| `loader` | Đọc `data/products.json`, trả về danh sách sản phẩm đã validate; cũng chứa `resolve_category()` và `effective_price()` dùng bởi `search` (OBSERVED) | File là **envelope dict**, sản phẩm nằm ở key `products` (OBSERVED) |
| `text` | Fold bỏ dấu + tách token | Nơi duy nhất biết về `đ`; xem Key decisions |
| `index` | Dựng `dict[token → set(product_id)]` lúc khởi động + `lookup()` tra AND tại request-time | Chỉ index `name` + `brand` + `category_name` |
| `search` | Khớp AND trên token, lọc category/brand/giá, sắp xếp, phân trang | Không biết gì về HTTP |
| `models` | Pydantic response models (product, phong bì phân trang, health check) | |
| `main` | Khai báo FastAPI app + 4 route | **Thứ tự khai báo route là load-bearing** |

Phụ thuộc một chiều, phân lớp (OBSERVED từ import thực tế): `text` là tầng đáy (không phụ
thuộc gì trong `app/`); `loader` và `index` đều phụ thuộc `text`; `search` phụ thuộc
`loader` + `index` + `text`; `main` phụ thuộc `search` + `index` + `loader` + `models` để
wiring route. Không phải một chuỗi tuyến tính đơn — `search` và `main` đều phụ thuộc trực
tiếp vào nhiều hơn một module tầng dưới. Tầng `search` trở xuống thuần túy, test được không
cần HTTP.

## Data flow

Khởi động: `loader` đọc envelope → lấy `d["products"]` → `text` fold từng blob
(`name` + `brand` + `category_name`) → `index` dựng token map. Xảy ra đúng một lần.

Mỗi request: query params → validate (Pydantic) → `search` tra token index (nếu có `q`)
→ lọc category/brand/giá → sort theo `id` tăng dần → đếm `total` **trước khi cắt trang**
→ cắt trang → serialize qua `models`.

State duy nhất là index trong RAM, dựng lại mỗi lần khởi động. Không cache, không
invalidation, không ghi.

## External dependencies

| Dependency | Vai trò | Trạng thái |
|---|---|---|
| `fastapi 0.139.2` / `starlette 1.3.1` / `uvicorn 0.51.0` | web framework + server | Đã cài (OBSERVED); `fastapi`+`uvicorn` chốt trong `pyproject.toml`, `starlette` là transitive dep của `fastapi` |
| `pydantic 2.13.4` | validation + serialization | Đã cài |
| `pytest 9.1.1` | test runner | Đã cài |
| `httpx2 2.7.0` | **bắt buộc** cho `starlette.testclient` | Đã cài — `httpx` KHÔNG thay thế được |
| `data/products.json` | nguồn dữ liệu duy nhất, bất biến | 28 KB, 20 sản phẩm |

Không datastore, không API bên thứ ba, không queue.

**Bẫy đã xác minh:** `starlette 1.3.1` ném `RuntimeError: The starlette.testclient module
requires the httpx2 package` — `httpx 0.28.1` trên máy là **sai gói**. Dev-deps phải ghi
`httpx2` ngay từ đầu, nếu không toàn bộ test API bị chặn.

## Boundaries & trust

Không có ranh giới tin cậy thật: chạy local, một người dùng, đọc-only, không secret.
Bề mặt duy nhất tiếp xúc đầu vào ngoài là query params của 4 endpoint — xử lý bằng
validation của Pydantic/FastAPI, trả 422 khi sai.

`data/products.json` là tài sản chỉ-đọc. Runtime **không bao giờ** mở nó ở chế độ ghi.

## Key decisions

1. **In-memory token index, khớp token nguyên vẹn, AND trên mọi token** (`DEC-1`).
   Chọn bằng phép đo trên chính dataset này: F1 **0.91** vs substring **0.71** vs
   tiền tố **0.77**. Substring bị loại vì `"may in"` trả **16/20** (do `"in"` nằm trong
   `"Inverter"`) thay vì đúng 4.

2. **Fold `đ→d` / `Đ→d` riêng, SAU bước NFD.** `unicodedata` NFD không đụng tới `đ` (không
   phải category `Mn`) — hỏng **8/20** sản phẩm, `"Ngăn đá"` fold ra `"ngan đa"` nên gõ
   `"ngan da"` ra **0 kết quả mà không ném lỗi**. Sau khi fold đúng: **0/20** còn sót.

3. **`GET /products/search` phải khai báo TRƯỚC `GET /products/{product_id}`.** Sai thứ tự
   trả **`200`** kèm `{"hit":"by_id","product_id":"search"}` — **không phải 404**. Hỏng thầm
   lặng: test chỉ assert status code sẽ PASS trong khi API đã hỏng.

4. **Thứ tự kết quả: `id` tăng dần**, cho cả `/products` lẫn `/products/search`. Thứ tự file
   KHÁC thứ tự sort (`refrigerator-358160` vs `air-conditioner-335837`, OBSERVED) nên phải
   chốt rõ. Sort theo `id` là contract độc lập với cách file được serialize.

5. **Không dùng SQLite FTS5** dù có sẵn (3.51.2). Lợi thế chính của nó là
   `remove_diacritics` dựng sẵn — nhưng vẫn hỏng ở `đ`, vẫn phải fold ở tầng ứng dụng.
   Đã phải tự fold thì FTS5 chỉ còn là schema thừa cho 28 KB dữ liệu tĩnh.

## Constraints & non-goals

**Trade-off đã chấp nhận, có chủ ý — không phải bug:**

- **Precision 0.83, 4 false positive.** `"tủ"` và `"từ"` cùng fold thành `"tu"`, nên
  `"tu lanh"` trả 8 kết quả: 4 tủ lạnh + 4 máy lạnh có `"Từ 30 - 40m²"` trong tên. Va chạm
  đồng tự cố hữu của bỏ dấu. Recall vẫn **1.00** — không sản phẩm đúng nào bị bỏ sót.
- **`"dien"` trả 0 kết quả** dù `"điện"` có trong `specifications` — hệ quả của việc chỉ
  index `name` + `brand` + `category_name`.
- **`printer-318469` biến mất khi lọc giá** — null cả `effective_price` lẫn `original_price`
  (sản phẩm duy nhất, OBSERVED). Vẫn hiện khi list/search.

**Non-goals:** frontend/UI · auth · database ngoài · cloud deployment · fuzzy search /
typo-tolerance · lọc theo `availability` (`"unknown"` 20/20) · ảnh (`image_url` null 20/20)
· write API · lọc theo `specifications` (43 key rời rạc) · sort tùy chọn theo giá.
