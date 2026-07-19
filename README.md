## Submission

Bài nộp được cung cấp dưới dạng GitHub Release.

- [Báo cáo insight khi sử dụng SDLC Harness](docs/HARNESS_INSIGHT.md)
- [Trang Release và file ZIP đầy đủ](https://github.com/dholmes0207/sdlc-harness-internship/releases)

File `sdlc-harness-internship-full.zip` trong Release được tạo từ toàn bộ
thư mục dự án, bao gồm cả các file runtime và các file bị Git ignore, theo
đúng yêu cầu của đề bài.

### Trạng thái bản nộp

Đây là bản nộp tạm thời. Quy trình hiện đã hoàn thành:

- Cài đặt và xác minh SDLC Harness.
- Discover.
- Plan và red-team.
- Human review và plan approval.
- Cook Phase 1 (Setup).
- Cook Phase 2 (Text Fold).
- Cook Phase 3 (Loader Models).
- Cook Phase 4 (Search Core).
- Cook Phase 5 (Api Routes).
- Cook Phase 6 (Docker Docs).

Kết quả test tổng thể, code review, ship và báo cáo cuối sẽ được cập nhật
trong bản Release hoàn chỉnh.

---

## Product Search API

API tìm kiếm sản phẩm bằng FastAPI, đọc dữ liệu tĩnh từ
`data/products.json` (20 sản phẩm), có accent fold tiếng Việt (khớp
`"ngan da"` với `"Ngăn đá"`), token index dựng lúc khởi động, lọc theo
category/brand/khoảng giá, và phân trang qua pagination envelope.

### Yêu cầu

- Python ≥ 3.13.
- Docker (tuỳ chọn, để chạy container).

### Setup

```bash
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python \
  fastapi==0.139.2 uvicorn==0.51.0 pydantic==2.13.4 \
  pytest==9.1.1 httpx2==2.7.0
```

Không dùng `uv`? Dùng venv chuẩn thư viện, cài đúng 5 dependency ghim bản
trên, **không** `-e .` và **không** `.`:

```bash
python3 -m venv .venv
.venv/bin/pip install \
  fastapi==0.139.2 uvicorn==0.51.0 pydantic==2.13.4 \
  pytest==9.1.1 httpx2==2.7.0
```

**Vì sao không cài project (`pip install -e .` / `pip install .`):** repo
này là ứng dụng chạy tại chỗ, không phải package phân phối. `pythonpath =
["."]` trong `pyproject.toml` là thứ khiến `import app.*` chạy được mà
không cần cài package — cài `-e .` mâu thuẫn với chính thiết kế đó. Hơn
nữa top-level của repo có bốn thư mục (`app/`, `data/`, `plans/`,
`harness/`), nên setuptools từ chối build với lỗi
`Multiple top-level packages discovered in a flat-layout`. Đây chính là
lý do `pyproject.toml` cố tình không có `[build-system]`.

### Chạy test

```bash
.venv/bin/python -m pytest tests/ -q
```

### Chạy server (local)

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Mở `http://127.0.0.1:8000/docs` để xem Swagger UI.

### Chạy bằng Docker

```bash
docker build -t product-search-api .
docker run -d --rm --name product-search-api-demo -p 8000:8000 product-search-api
# đợi /health sẵn sàng (uvicorn cần vài trăm ms để bind cổng)
for i in $(seq 1 30); do curl -sf localhost:8000/health >/dev/null && break; sleep 0.5; done
```

Dọn dẹp sau khi xong (bắt buộc, nếu không cổng 8000 vẫn bị chiếm):

```bash
docker stop product-search-api-demo
```

Image chỉ cài 3 dependency runtime đã ghim (`fastapi`, `uvicorn`,
`pydantic`) — không có `pytest`, không cài project như package
(`pip install .` sẽ vỡ build vì flat-layout của repo này). Dữ liệu
`data/products.json` được COPY thẳng vào image, không mount volume.

### Ví dụ `curl` (OBSERVED, chạy thật trên container Docker)

| # | Lệnh | Kết quả thật |
|---|---|---|
| 1 | `curl 'localhost:8000/health'` | `{"status":"ok","products_loaded":20}` |
| 2 | `curl 'localhost:8000/products?page_size=10'` | `total: 20`, 10 items |
| 3 | `curl 'localhost:8000/products/search?q=ngan%20da'` | `total: 3` |
| 4 | `curl 'localhost:8000/products/search?q=may%20in'` | `total: 4` |
| 5 | `curl 'localhost:8000/products?category=tu%20lanh'` | `total: 4` |
| 6 | `curl 'localhost:8000/products?min_price=0&page_size=100'` | `total: 19` |
| 7 | `curl 'localhost:8000/products/printer-318469'` | `200`, `id` khớp `printer-318469` |
| 8 | `curl 'localhost:8000/products/nope'` | `404` |
| 9 | `curl 'localhost:8000/products/search?q=%20%20'` | `422` |

### Trade-off đã biết (không phải bug)

- `"tu lanh"` trả **8** kết quả (4 tủ lạnh + 4 máy lạnh) — va chạm đồng tự
  của accent fold giữa `tủ`/`từ`. Precision 0.83 / recall 1.00. Có chủ ý,
  đánh đổi để không bỏ sót kết quả đúng.
- `"dien"` trả **0** dù `"điện"` xuất hiện trong `specifications` — token
  index chỉ dựng từ `name` + `brand` + `category_name`, không đụng tới
  `specifications`.
- `printer-318469` biến mất khỏi kết quả khi lọc theo giá
  (`min_price`/`max_price`) — sản phẩm **duy nhất** trong dataset có cả
  `effective_price` lẫn `original_price` đều null. Sản phẩm này vẫn hiện
  bình thường khi list/search không lọc giá, và vẫn tra được qua
  `GET /products/{id}`.