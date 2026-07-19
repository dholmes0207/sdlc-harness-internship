---
phase: 6
title: "Docker Docs"
status: pending
plan: 260719-1435-product-search-api
created: 2026-07-19
harness_version: 5.3.0
harness_kit_digest: 251ed307796039124b44d71759b3f62d8bb9135c4bf3053156e38798587a50a8
harness_schema_version: 1.0
---

# Phase 6 — Docker Docs

## Overview

Đóng gói + tài liệu: `Dockerfile` chạy local tái lập được, `README.md` với
setup / test / run / ví dụ `curl`, và **xác minh `/docs` lên thật** trên
uvicorn thật lẫn trong container — không phải qua `TestClient`.

Đây là phase duy nhất có tiêu chí **manual**: `TestClient` đã chứng minh
`/docs` trả `200` (OBSERVED), nhưng "trả 200" khác "Swagger UI render được
trong trình duyệt". Phần chênh lệch đó cần mắt người.

Dockerfile **COPY** `data/products.json` vào image (quyết định #14) — dữ liệu
tĩnh, bất biến, tái lập được. Không mount volume.

**Phụ thuộc:** phase 5.

## Files

**Create**

- `Dockerfile`
- `.dockerignore`
- `tests/api/test_docs.py`

**Modify**

- `README.md` — **file này ĐÃ TỒN TẠI** (OBSERVED: 105 byte, đã commit, nội dung
  hiện tại chỉ là tiêu đề `# SDLC Harness Internship` + một dòng mô tả). Phase này
  **mở rộng** nó, **không tạo mới** và **không xoá** phần mô tả dự án sẵn có.

Không chạm file nào của phase khác. Không sửa code trong `app/`, **không** sửa
`tests/api/test_routes.py` (thuộc phase 5) — test mới của phase này nằm ở file
riêng `tests/api/test_docs.py` để giữ quyền sở hữu file rời nhau.

## Requirements

### `Dockerfile`

1. Base `python:3.13-slim` — **OBSERVED**, đã pull thật trong phiên lập kế hoạch
   (plan.md VL-10). Digest
   `sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91`,
   43 MB, Python **3.13.14** bên trong. Không còn là giả định, bỏ fallback.
2. **COPY** `data/products.json` vào image (quyết định #14). Không volume, không
   bind mount.

   **Layout trong image phải khớp `DEFAULT_DATA_PATH` của P3.** Loader tính
   đường dẫn là `Path(__file__).resolve().parent.parent / "data" / "products.json"`
   (VL-19), nên với `WORKDIR /app`:

   ```dockerfile
   WORKDIR /app
   COPY app/  /app/app/
   COPY data/ /app/data/
   ```

   → `__file__` = `/app/app/loader.py` → `.parent.parent` = `/app` →
   `/app/data/products.json`. Đặt lệch cấu trúc này thì loader **fail-fast**
   lúc khởi động container dù file vẫn nằm trong image.
3. Cài dependency bằng **cùng bộ số đã ghim ở phase 1**, và **KHÔNG**
   `pip install -e .` / `pip install .` (quyết định **D-B**, RK15):

   ```dockerfile
   RUN pip install --no-cache-dir \
         fastapi==0.139.2 uvicorn==0.51.0 pydantic==2.13.4
   ```

   **Cùng nguyên nhân đã giết phase 1.** Đã chạy thật: cài project ở repo này
   khiến setuptools từ chối build với
   `Multiple top-level packages discovered in a flat-layout: ['app','data','plans','harness']`.
   Trong container lỗi này còn khó đọc hơn vì nó bung ra giữa `docker build`.

   `app/` được đưa vào image bằng **`COPY`**, không phải bằng cài package.
   `WORKDIR /app` + `COPY app/ /app/app/` là đủ để `uvicorn app.main:app` chạy.
4. `EXPOSE 8000`, `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`.
5. **Không** cài dev-deps (`pytest`, `httpx2`) vào image runtime.
6. `.dockerignore` loại `.venv/`, `__pycache__/`, `.pytest_cache/`, `tests/`,
   `plans/`, `harness/`, `.git/`.

### `README.md`

7. **MỞ RỘNG file sẵn có, không ghi đè.** Nội dung hiện tại (tiêu đề
   `# SDLC Harness Internship` + dòng mô tả dự án) **phải giữ lại** — nó nói về
   bối cảnh internship, không phải về API. Thêm các mục bên dưới nó: mô tả ngắn
   API · yêu cầu · setup · chạy test · chạy server · Docker · ví dụ `curl` ·
   trade-off đã biết.
8. Ví dụ `curl` **phải chạy được** và ghi kèm **kết quả mong đợi bằng số**.
   Bảng có **9** dòng, trong đó **đúng 5** dòng mang giá trị `total:` — chỉ 5
   dòng đó là thứ `test_readme_curl_examples_match_reality` parse được:

   | # | Lệnh | Kết quả mong đợi (OBSERVED) | Parse được `total`? |
   |---|---|---|---|
   | 1 | `curl 'localhost:8000/health'` | `products_loaded: 20` | không |
   | 2 | `curl 'localhost:8000/products?page_size=10'` | `total: 20`, 10 items | **có** |
   | 3 | `curl 'localhost:8000/products/search?q=ngan%20da'` | `total: 3` | **có** |
   | 4 | `curl 'localhost:8000/products/search?q=may%20in'` | `total: 4` | **có** |
   | 5 | `curl 'localhost:8000/products?category=tu%20lanh'` | `total: 4` | **có** |
   | 6 | `curl 'localhost:8000/products?min_price=0&page_size=100'` | `total: 19` | **có** |
   | 7 | `curl 'localhost:8000/products/printer-318469'` | `200`, id khớp | không |
   | 8 | `curl 'localhost:8000/products/nope'` | `404` | không |
   | 9 | `curl 'localhost:8000/products/search?q=%20%20'` | `422` | không |

9. Mục **trade-off đã biết** phải ghi rõ ba điều, để người đọc sau không tưởng
   là bug:
   - `"tu lanh"` trả **8** kết quả (4 tủ lạnh + 4 máy lạnh) — va chạm đồng tự
     `tủ`/`từ`, precision 0.83 / recall 1.00. Có chủ ý.
   - `"dien"` trả **0** dù `"điện"` có trong `specifications` — chỉ index
     `name` + `brand` + `category_name`.
   - `printer-318469` biến mất khi lọc giá — null cả hai trường giá, là sản
     phẩm **duy nhất** như vậy. Có chủ ý.

10. Dùng đúng thuật ngữ `docs/glossary.yaml`: `accent fold`, `token index`,
    `pagination envelope`, `effective price`. **Cấm** `normalize`, `slugify`,
    `search index`, `inverted index`, `wrapper`, `result set`, `final price`,
    `real price`.

## Implementation Steps

1. Viết `tests/api/test_docs.py` (xem TDD) → chạy → **đỏ**.
2. Viết `.dockerignore`.
3. Viết `Dockerfile` theo Requirements #1-6.
4. `docker build -t product-search-api .`
5. **Chạy container ở chế độ NỀN (`-d`), có đặt tên.** Không dùng foreground:

   ```bash
   docker run -d --rm --name product-search-api-demo \
     -p 8000:8000 product-search-api
   ```

   **Vì sao bắt buộc `-d`:** `docker run` foreground **chiếm luôn terminal** —
   `uvicorn` chạy ở tiền cảnh và không bao giờ trả prompt, nên bước 7 (chạy
   `curl`) **không thể chạy trong cùng phiên**. Một agent thực thi tuần tự sẽ
   treo ở đây cho tới khi timeout. `-d` trả về ngay container id.

6. **Poll `/health` cho tới khi container sẵn sàng** — đừng `curl` ngay, uvicorn
   cần vài trăm ms để bind cổng, và một `curl` sớm sẽ fail vì
   `Connection refused` chứ không phải vì API sai:

   ```bash
   for i in $(seq 1 30); do
     curl -sf localhost:8000/health >/dev/null && break
     sleep 0.5
   done
   ```

   Quá 30 lần (15 giây) mà chưa lên → coi như **thất bại**, đọc
   `docker logs product-search-api-demo` để lấy nguyên nhân thật (khả năng cao là
   loader fail-fast vì layout `data/` trong image sai — xem Requirements #2).

7. Chạy **toàn bộ 9 lệnh `curl`** ở bảng trên với container đang chạy, đối chiếu
   từng con số.
8. Mở `http://127.0.0.1:8000/docs` trong trình duyệt, xác nhận Swagger UI render
   và thử được cả 4 endpoint (tiêu chí **manual**).
9. **LUÔN dọn dẹp, kể cả khi các bước trên thất bại:**

   ```bash
   docker stop product-search-api-demo
   ```

   `--rm` chỉ xoá container **sau khi nó dừng**, nên thiếu `docker stop` thì
   container còn sống và **giữ cổng 8000**, làm mọi lần chạy lại sau đó chết với
   `port is already allocated`. Đặt tên cố định (`--name`) chính là để bước dọn
   này luôn nhắm đúng mục tiêu.

10. Cập nhật `README.md` (mở rộng file sẵn có), dán kết quả `curl` **thật** vào —
    không bịa số.

## TDD

Phase này chủ yếu là đóng gói/tài liệu, nhưng **không** miễn gate.

### Tests-before (RED)

Cả hai test nằm ở **file mới** `tests/api/test_docs.py`, dùng lại fixture
`client` từ `tests/api/conftest.py` (phase 5) — nghĩa là vẫn **bắt buộc**
`with TestClient(app) as c:`, xem RK3.

- [ ] `tests/api/test_docs.py::test_openapi_envelope_schema_complete` — assert
      schema `PaginationEnvelope` trong `/openapi.json` khai đủ **4** trường
      `total`, `page`, `page_size`, `items`. Khoá: `/docs` render **đúng hợp
      đồng**, không phải chỉ "trả 200".
      (Việc đếm **4** path đã do `test_api_openapi_lists_four_paths` ở phase 5
      lo — không lặp lại ở đây.)
- [ ] `tests/api/test_docs.py::test_readme_curl_examples_match_reality` —
      parse `README.md`, trích các cặp (đường dẫn, `total` mong đợi) ở bảng
      curl, rồi gọi **chính** các đường dẫn đó qua fixture `client` và assert
      `total` khớp. Khoá: README không bị lệch khỏi hành vi thật — chế độ hỏng
      phổ biến nhất của tài liệu.

      **BẮT BUỘC assert số cặp parse được TRƯỚC vòng lặp:**

      ```python
      pairs = parse_curl_table(Path("README.md").read_text())
      assert len(pairs) == 5, f"parse được {len(pairs)} cặp, phải là 5"
      for path, expected_total in pairs:
          ...
      ```

      **Không có dòng assert này thì test PASS RỖNG.** Nếu regex parse ra **0**
      cặp — vì bảng đổi format, vì escape sai, vì ai đó viết lại README —
      vòng lặp không chạy lần nào và test **xanh trong khi không kiểm gì cả**.
      Nó là guard **duy nhất** chống số bịa trong README, nên một guard tự vô
      hiệu hoá âm thầm còn tệ hơn không có (VL-21).

      Con số **5** khớp bảng Requirements #8: 9 dòng curl, **5** dòng mang
      `total:`. Thêm/bớt dòng có `total` thì phải sửa cả hai chỗ — đó là chủ ý,
      nó buộc người sửa README nhìn vào test.

      **Lý do ĐỎ — KHÔNG phải `FileNotFoundError`.** `README.md` đã tồn tại sẵn
      trong repo (105 byte, đã commit), nên `Path("README.md").read_text()` chạy
      bình thường. Test đỏ vì **README chưa có bảng `curl`**:

      ```
      AssertionError: parse được 0 cặp, phải là 5
      ```

      Đây chính là dòng `assert len(pairs) == 5` ở trên bắn ra. Ghi kỳ vọng là
      `FileNotFoundError` thì RED **đỏ sai lý do** — và tệ hơn, nếu ai đó "sửa"
      test cho khớp kỳ vọng sai đó, guard duy nhất chống số bịa trong README sẽ
      bị vô hiệu hoá (VL-26).

### Implement

Các bước 2-8 ở `## Implementation Steps`.

### Tests After

- [ ] Hai test trên xanh.
- [ ] Toàn bộ suite vẫn xanh — phase này **không** được sửa gì trong `app/`.

### Regression Gate

```
.venv/bin/python -m pytest tests/ -q
```

**MUST PASS 100%.** Commit `docs: add Dockerfile and README with verified curl examples`.

## Success

**Tự động (test / invariant):**

- [ ] `.venv/bin/python -m pytest tests/ -q` → **0 failed** toàn suite.
- [ ] `/openapi.json` có **đúng 4** path.
- [ ] `test_readme_curl_examples_match_reality` xanh **và** parse được **đúng 5**
      cặp (`assert len(pairs) == 5`) — **5/9** dòng curl mang `total:`; 4 dòng
      còn lại kiểm bằng status code, không qua `total` (VL-21).
- [ ] `docker build -t product-search-api .` exit **0**.
- [ ] Container demo chạy **nền** (`docker run -d --rm --name product-search-api-demo`)
      và `/health` phản hồi trong **≤ 15 giây** qua vòng poll — không dùng
      foreground (VL-28).
- [ ] **Dọn dẹp đã chạy:** sau khi xong,
      `docker ps --filter name=product-search-api-demo --format '{{.Names}}'`
      trả **rỗng**. Container còn sống sẽ giữ cổng 8000 và làm lần chạy sau chết
      với `port is already allocated`.
- [ ] `docker run --rm product-search-api python -c "import pytest"` **thất bại**
      — chứng minh dev-deps **không** lọt vào image runtime.
      (Hai lệnh `docker run --rm ... python -c` này chạy một phát rồi thoát ngay,
      không bind cổng — **không** cần `-d`.)
- [ ] `docker run --rm product-search-api python -c "import importlib.metadata as m; m.version('product-search-api')"`
      **thất bại** với `PackageNotFoundError` — chứng minh image **không** cài
      project như package (RK15).
- [ ] Trong container: `ls /app/data/products.json` exit **0** (quyết định #14
      — dữ liệu nằm trong image, không phụ thuộc mount).
- [ ] Container chạy: `curl -s localhost:8000/health` → `products_loaded: **20**`
      (không phải 90 — quyết định #17).
- [ ] Container chạy: `curl -s 'localhost:8000/products/search?q=ngan%20da'` →
      `total: 3`.
- [ ] Container chạy: `curl -s 'localhost:8000/products?category='` →
      `total: 0` (không phải 20 — quyết định #15).
- [ ] `git diff --name-only HEAD~1 -- app/` → **0 dòng** (phase này không đụng code).
- [ ] `grep -riE "normalize|slugify|inverted index|search index|wrapper|result set|final price|real price" README.md`
      → **0 dòng** (từ cấm `docs/glossary.yaml`).

**Manual — cần mắt người, bind `manual_test_anchor.py`:**

- [ ] **A15.** Chạy `uvicorn app.main:app` trên máy trần, mở
      `http://127.0.0.1:8000/docs` trong trình duyệt: Swagger UI **render**
      (không phải trang trắng / lỗi JS), liệt kê đủ **4** endpoint, và nút
      "Try it out" gọi được `/products/search?q=may in` trả **4** kết quả.
      Lặp lại y hệt với container Docker đang chạy.
      Anchor: `manual_test_anchor.py`.

      Vì sao là manual: `TestClient` chỉ chứng minh `/docs` trả HTTP **200**
      (OBSERVED). Nó **không** chứng minh Swagger UI nạp được asset và render.
      Không có automated check nào đáng dựng cho việc này ở quy mô này.

## Risks

| Rủi ro | Khả năng | Tác động | Xử lý |
|---|---|---|---|
| README lệch khỏi hành vi thật | **M** | **M** — người chấm chạy curl theo README, ra số khác, mất tin cậy | `test_readme_curl_examples_match_reality` biến README thành test |
| **Test README PASS RỖNG (regex parse 0 cặp)** | **M** | **H** — guard **duy nhất** chống số bịa tự vô hiệu hoá âm thầm; xanh mà không kiểm gì | `assert len(pairs) == 5` **trước** vòng lặp. Bảng đổi format → test đỏ ngay thay vì lặng lẽ ngừng kiểm (VL-21) |
| Dán kết quả curl **bịa** thay vì chạy thật | M | **H** — vi phạm trực tiếp `harness/rules/verification-mechanism.md` | Bước 6 bắt chạy 9 lệnh với container thật **trước** khi viết README; test parse lại đối chiếu |
| **`pip install .` trong Dockerfile (RK15)** | **M** | **C** — `docker build` chết vì flat-layout, giống hệt phase 1 | Requirements #3 cài thẳng 3 dep runtime; `app/` vào image bằng `COPY`. Success criteria kiểm `PackageNotFoundError` trong container |
| Layout trong image lệch `DEFAULT_DATA_PATH` | M | **H** — loader fail-fast lúc khởi động container dù file có trong image | Requirements #2 ghim `WORKDIR /app` + `COPY app/ /app/app/` + `COPY data/ /app/data/`; kiểm `ls /app/data/products.json` |
| ~~`python:3.13-slim` không pull được~~ | **loại bỏ** | — | Rủi ro này đã **đóng**: pull thật thành công trong phiên lập kế hoạch (43 MB, Python 3.13.14). Không còn `[PRIOR]`, không cần fallback |
| Quên COPY `data/products.json` | M | **H** — container khởi động chết ở fail-fast của loader | Success criteria kiểm `ls /app/data/products.json` **bên trong** container |
| Dev-deps lọt vào image runtime | M | L — image phình, lệch chuẩn | Success criteria: `import pytest` trong container phải **thất bại** |
| Sửa code `app/` trong phase docs | L | M — trộn scope, khó revert | `git diff --name-only HEAD~1 -- app/` phải trả 0 dòng |
| Bản dep trong Docker khác venv local | L | M — "chạy máy tôi thì được" | Cài từ `pyproject.toml` đã ghim ở phase 1, cùng bộ số |
| README mô tả trade-off `tủ`/`từ` là bug | L | L | Requirements #9 bắt ghi rõ là có chủ ý, kèm số đo |
