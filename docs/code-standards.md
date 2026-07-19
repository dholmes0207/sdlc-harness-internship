# Code Standards

> Kỷ luật code chung cho repo này. `hs:plan` và `hs:cook` ĐỌC file này trước khi làm việc.
> Giữ ngắn và đúng.

## Languages & conventions

Python 3.13 (OBSERVED: 3.13.13). Không thêm ngôn ngữ thứ hai.

- `snake_case` cho hàm/biến/module, `PascalCase` cho class và Pydantic model.
- Type hints **bắt buộc** trên mọi hàm public. Pydantic v2 API (`model_validate`,
  `model_dump`) — không dùng API v1 đã bỏ.
- Định danh trong code viết bằng **tiếng Anh**; comment và tài liệu viết tiếng Việt.
- Hàm thuần túy (fold, tokenize, filter, paginate) **không được** biết gì về HTTP. Chỉ
  tầng route mới import `fastapi`.
- Không catch trống (`except:` / `except Exception: pass`).

**Quy tắc đặt tên có bẫy đã biết:** dataset có sẵn key `total` (= 20, tổng số bản ghi
trong file) trùng tên với `total` của phong bì phân trang (= số bản ghi khớp sau lọc,
trước khi cắt trang). Hai khái niệm khác nhau — không được dùng lẫn. Trong code, đọc
dataset qua tên rõ ràng, đừng truyền thẳng `data["total"]` vào response.

## Project layout

```
app/
  main.py       # FastAPI app + khai báo route (thứ tự route là load-bearing)
  models.py     # Pydantic response models
  loader.py     # đọc envelope data/products.json -> list sản phẩm
  text.py       # fold bỏ dấu + tokenizer (nơi DUY NHẤT biết về 'đ')
  index.py      # dict[token -> set(product_id)]
  search.py     # khớp, lọc, sort, phân trang
data/products.json   # chỉ-đọc, không bao giờ ghi lúc runtime
tests/
  unit/         # test thuần túy: fold, tokenizer, lọc, biên phân trang
  api/          # test qua TestClient (cần httpx2)
pyproject.toml
Dockerfile
README.md
```

Logic fold bỏ dấu sống ở **đúng một chỗ** (`app/text.py`). Nhân bản nó là lỗi review.

## Testing

`pytest`. TDD bắt buộc: viết test đỏ trước, code cho xanh sau. Không viết implementation
trước rồi bổ test.

Gate: **100% test pass** trước khi sang phase kế tiếp. Không skip, không `xfail` để lách.

**Bốn test hồi quy bắt buộc** — đây là các probe dùng-một-lần từ phiên discovery, không
hóa thành test thì mất sạch. Mỗi cái khóa một chế độ hỏng đã đo được:

1. `"ngan da"` **phải** khớp `"Ngăn đá"` — khóa việc fold `đ`. Naive NFD cho **0 hit**,
   fold đúng cho **3 hit**. Test quan trọng nhất bộ test.
2. `"may in"` **phải** trả **đúng 4** sản phẩm, không phải 16 — khóa khớp token nguyên vẹn,
   chặn ai đó "đơn giản hóa" về substring.
3. `/products/search` **phải** assert vào **thân phản hồi**, không chỉ status code — sai thứ
   tự route trả `200` kèm `product_id="search"`, nên assert status-only sẽ PASS trong khi
   API đã hỏng.
4. `printer-318469` **phải hiện** khi list và **bị ẩn** khi có `min_price`/`max_price` —
   khóa luật giá null (sản phẩm duy nhất null cả hai trường).

Ngoài ra: `page_size=10` trên 20 sản phẩm phải cho `total=20` và `len(items)=10` — khóa
việc `total` không bị trả nhầm thành `len(items)`.

Test phải assert vào **giá trị cụ thể quan sát được** (số lượng, id, nội dung), không phải
"không ném lỗi".

## Error handling & logging

- Đầu vào sai của người gọi → **422**, để FastAPI/Pydantic tự sinh. Không tự chế format lỗi
  song song.
- `q` rỗng hoặc chỉ khoảng trắng → **422** (dùng `GET /products` để lấy toàn bộ).
- `product_id` không tồn tại → **404** với thân lỗi rõ ràng.
- Trang vượt quá `total` → **`items: []`** kèm `total` đúng, **không** 404. Trang rỗng là
  kết quả hợp lệ.
- Lỗi nạp dữ liệu lúc khởi động → **fail fast**, không khởi động lên với index rỗng. Một API
  chạy được nhưng trả 0 kết quả cho mọi truy vấn là chế độ hỏng tệ hơn crash.
- Logging: stdlib `logging`, không `print()` trong code ứng dụng.

## Security & secrets

Không secret trong repo — không auth, không API key, không credential. Nếu điều đó đổi,
đọc từ biến môi trường, không hardcode.

Đầu vào ngoài chỉ có query params; validate bằng Pydantic, ràng buộc biên rõ ràng
(`page` ≥ 1; `page_size` ≥ 1 và ≤ 100).

Dependency: chốt phiên bản trong `pyproject.toml`. Không thêm dependency runtime ngoài
`fastapi` + `uvicorn` + `pydantic` mà không có lý do ghi lại. `httpx2` là **dev-dep bắt
buộc** (`starlette.testclient` đòi nó; `httpx` không thay thế được).

## Commits & review

Conventional commits: `feat:`, `fix:`, `test:`, `chore:`, `docs:`, `refactor:`.

Nhánh làm việc hiện tại: `feat/product-search-api`. Không commit thẳng lên `main`.

Không commit `harness/state/` (đã gitignore).

Review chặn merge khi: fold bỏ dấu bị nhân bản ra ngoài `app/text.py` · thứ tự route bị đảo
· test API chỉ assert status code · bất kỳ test hồi quy nào trong 4 test trên bị xóa hoặc
làm yếu đi · runtime ghi vào `data/products.json`.
