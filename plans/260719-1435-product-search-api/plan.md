---
id: 260719-1435-product-search-api
title: "Product Search API"
status: completed
mode: hard
tdd: true
branch: feat/product-search-api
created: 2026-07-19
author: user:duyluuhx07@gmail.com
decisions: [DEC-1, DEC-2, DEC-3]
phases:
  - phases/phase-1-setup.md
  - phases/phase-2-text-fold.md
  - phases/phase-3-loader-models.md
  - phases/phase-4-search-core.md
  - phases/phase-5-api-routes.md
  - phases/phase-6-docker-docs.md
harness_version: 5.3.0
harness_kit_digest: 251ed307796039124b44d71759b3f62d8bb9135c4bf3053156e38798587a50a8
harness_schema_version: 1.0
---

# Plan: Product Search API

> Hợp đồng cho `hs:cook`. Mọi con số trong file này đều **OBSERVED** — chạy lại
> trực tiếp trên `data/products.json` và trên FastAPI 0.139.2 / starlette 1.3.1
> thật trong phiên lập kế hoạch này. Claim không có anchor được tag
> `[ASSUMED]` / `[PRIOR]`.

## Tổng quan

Xây HTTP API đọc-only bằng FastAPI, phục vụ tìm kiếm **20 sản phẩm** điện máy
tiếng Việt từ `data/products.json` (28 KB, tĩnh, bất biến lúc runtime). Bốn
endpoint: `GET /health`, `GET /products`, `GET /products/search`,
`GET /products/{product_id}`.

Bài toán lõi **không phải quy mô** mà là chất lượng khớp text tiếng Việt không
dấu. Rủi ro kỹ thuật tụ vào **một họ chế độ hỏng duy nhất: hỏng thầm lặng trả
`200`** — API trả `200` với kết quả sai hoặc rỗng, nên **test chỉ assert status
code sẽ XANH trong khi API đã hỏng**. Bảng `## Risks` liệt kê các thành viên đã
biết của họ này; danh sách đó **mở**, không phải một con số cố định — mỗi lượt
review đến nay đều tìm thêm được. Vì vậy luật là **luật, không phải đếm**: mọi
test API phải assert vào **thân phản hồi**, không bao giờ chỉ status code.

Kiến trúc đã chốt ở `docs/system-architecture.md`; quyết định search core là
`DEC-1` (`docs/decisions.md`).

Scope đã cắt YAGNI: không UI, không auth, không DB ngoài, không fuzzy search.
Repo hiện **không có dòng code nào** — không `pyproject.toml`, không `app/`,
không `tests/` (OBSERVED: `ls` trả `No such file or directory` cho cả bốn).

### Data flow

**Khởi động (đúng một lần):**

```
data/products.json  --loader-->  envelope dict d
                                 d["products"] -> list[dict] (20 phần tử)
                                 d["categories"] -> list[dict] (5 phần tử)
       |                                |
       |                                +--> category_map: fold(name)|slug -> slug
       v
  text.fold(name + " " + brand + " " + category_name)
       |
       v
  text.tokenize -> set[str]
       |
       v
  index: dict[token -> set[product_id]]   (giữ trong RAM, không ghi đĩa)
```

**Mỗi request:**

```
query params -> Pydantic ListParams/SearchParams (422 nếu sai)
   -> search.run(): q -> token index -> AND trên mọi token -> set[id]
   -> lọc category (qua category_map) -> lọc brand (fold) -> lọc giá (effective price)
   -> sort theo id tăng dần
   -> total = len(kết quả)            <-- ĐẾM TRƯỚC KHI CẮT TRANG
   -> cắt [(page-1)*page_size : page*page_size]
   -> models.PaginationEnvelope -> JSON
```

State duy nhất: token index trong RAM, dựng lại mỗi lần khởi động. Không cache,
không invalidation, **không bao giờ ghi** vào `data/products.json`.

### Dependency graph

```
P1 setup ──> P2 text-fold ──> P3 loader-models ──> P4 search-core
                 └──────────────────────────────────┘
                        (P4 cần cả P2 lẫn P3)
P4 ──> P5 api-routes ──> P6 docker-docs
```

**Chuỗi hoàn toàn tuyến tính.** P3 **KHÔNG** độc lập với P2: `build_category_map`
và `resolve_category` ở `app/loader.py` gọi `app.text.fold` (phase-3 Requirements
#3, #3b) — `app/text.py` là tài sản của P2. Cạnh `{from: P2, to: P3}` có trong
`plan-graph.yaml`; batch dẫn xuất là `[[P1],[P2],[P3],[P4],[P5],[P6]]`.

Bản kế hoạch trước khẳng định P2 ⊥ P3. **Sai** — sửa ở VL-14. Nó chạy tuần tự
nên không hỏng gì trên thực tế, nhưng sidecar máy-đọc-được đang khẳng định một
antichain không tồn tại; ai bật `--parallel` sau này sẽ đọc phải khẳng định sai.

Plan này **sequential-only** — không emit dependency matrix / file-ownership
table kiểu `--parallel`.

### File ownership (mỗi file thuộc đúng một phase)

| Phase | Files sở hữu |
|---|---|
| 1 | `pyproject.toml`, `app/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/api/__init__.py`, `tests/unit/test_smoke.py`, `.gitignore` (modify) |
| 2 | `app/text.py`, `tests/unit/test_text.py` |
| 3 | `app/loader.py`, `app/models.py`, `tests/unit/test_loader.py`, `tests/unit/test_models.py` |
| 4 | `app/index.py`, `app/search.py`, `tests/unit/test_index.py`, `tests/unit/test_search.py` |
| 5 | `app/main.py`, `tests/api/conftest.py`, `tests/api/test_routes.py` |
| 6 | `Dockerfile`, `.dockerignore`, `tests/api/test_docs.py` · **`README.md` (modify — file đã tồn tại)** |

Không phase nào chạm file của phase khác. Zero overlap.

## Quyết định đã khoá

Không re-litigate. Nguồn: brief §5.1/§5.3 + bốn câu hỏi mở đã được người dùng
chốt trong phiên này.

| # | Quyết định | Nguồn |
|---|---|---|
| 1 | In-memory token index · fold `đ→d` riêng SAU NFD · khớp **token nguyên vẹn** | `DEC-1` |
| 2 | Chỉ index `name` + `brand` + `category_name`. **Không** `specifications` | brief §5.1 #2 |
| 3 | Nhiều token → **AND** | brief §5.1 #3 |
| 4 | `q` bắt buộc, không rỗng, **không chỉ khoảng trắng** → else **422** | brief §5.1 #4 |
| 5 | Lọc giá dùng `effective_price`, fallback `original_price`. Null cả hai → **hiện** khi list/search, **bị loại** khi có `min_price`/`max_price` | brief §5.1 #5 |
| 6 | `page` ≥1 mặc định 1; `page_size` ≥1 ≤100 mặc định 10. Phong bì: `total, page, page_size, items`. `total` đếm **trước** khi cắt trang | brief §5.1 #6 |
| 7 | Sort **`id` tăng dần**, cả `/products` lẫn `/products/search` | brief §5.2 |
| 8 | 4 endpoint | brief §5.1 #8 |
| 9 | `/products/search` khai báo **TRƯỚC** `/products/{product_id}` | brief §5.1 #9 |
| 10 | **`category` nhận CẢ HAI**: slug (`refrigerator`) VÀ tên hiển thị đã fold (`tu lanh`, `Tủ Lạnh`, `TU LANH`). Dựng `category_map` lúc khởi động từ `d["categories"]` | chốt phiên này |
| 11 | `brand` lọc theo fold, không phân biệt hoa thường | chốt phiên này |
| 12 | `min_price > max_price` → **422** | chốt phiên này |
| 13 | `page` vượt `total` → `items: []` kèm `total` đúng, **không** 404 | chốt phiên này |
| 14 | Dockerfile **COPY** `data/products.json` vào image | chốt phiên này |
| 15 | **Bộ lọc kích hoạt theo `is not None`, KHÔNG theo truthiness.** `?category=` (giá trị rỗng) bind thành `""`, không phải `None` — `if category:` bỏ qua bộ lọc (**20** kết quả), `if category is not None:` áp bộ lọc → miss → **0**. Chốt: **0** | red-team H1, chốt phiên này |
| 16 | **Biên giá là inclusive**: `>= min_price` và `<= max_price` | red-team M3, chốt phiên này |
| 17 | `products_loaded` của `/health` = `len(dataset.products)` (**20**), **không** `len(index)` (**90**) | red-team H5, chốt phiên này |

### Quyết định triển khai chốt trong lúc lập kế hoạch (có probe)

**`D-x` là nhãn CỤC BỘ của plan này, KHÔNG phải id trong sổ đăng ký DEC.** Hai hệ
thống đánh số khác nhau, đừng lẫn:

| Nhãn plan | Đã đăng ký DEC? | Ánh xạ |
|---|---|---|
| **D-B** (không cài project như package) | **CÓ** → `DEC-2` | trùng khớp 1-1 |
| **D-C** (`resolve_category` sở hữu việc fold đầu vào) | **một phần** → nằm dưới `DEC-3` | `DEC-3` bao quyết định #10 + #15; D-C là cơ chế triển khai bên dưới |
| **D-A** (Pydantic query model) | **KHÔNG** | chỉ cục bộ plan — chi tiết triển khai, không phải quyết định kiến trúc |
| **D-D** (ghim `response_model=`) | **KHÔNG** | chỉ cục bộ plan |

Sổ SSOT là `docs/decisions.md`, hiện có **`DEC-1`, `DEC-2`, `DEC-3`** (đăng ký qua
`decision_register.py`, không sửa tay). Frontmatter `decisions:` của plan này liệt
kê đủ cả ba. Không viết `D-A`/`D-D` như thể chúng là DEC — chúng chưa bao giờ được
đăng ký, và gọi vậy sẽ tạo ra id ma không tồn tại trong sổ.

**D-A — `ListParams` / `SearchParams(ListParams)` là Pydantic query model, không
phải danh sách `Query()` rời.**

Lý do: quyết định #12 (`min_price > max_price` → 422) là **validation liên
trường**, `Query()` rời không làm được. `docs/code-standards.md:72` cấm tự chế
format lỗi song song, nên `raise HTTPException(422, ...)` thủ công bị loại — nó
cho thân lỗi khác hẳn 422 của Pydantic. Dùng `model_validator(mode="after")`
cho 422 native.

**OBSERVED, và đây là bẫy mới phát hiện trong phiên này:** trộn một param
thường với một query model trong CÙNG endpoint thì **hỏng** —

```python
def search(q: Annotated[str, Query(...)], f: Annotated[Filters, Query()])
```

→ FastAPI coi `f` là field bắt buộc tên `"f"`, trả **422**
`{"loc": ["query", "f"], "msg": "Field required"}` cho **mọi** request, kể cả
request hợp lệ. Không phải giả thuyết — đã chạy thật.

Cách đúng đã probe xanh: `q` nằm **bên trong** model, qua kế thừa.

```python
class ListParams(BaseModel):
    category: str | None = None
    brand: str | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def check_price_range(self): ...   # min>max -> ValueError -> 422

class SearchParams(ListParams):
    q: str = Field(min_length=1)

    @field_validator("q")
    @classmethod
    def q_not_blank(cls, v): ...       # v.strip() rỗng -> ValueError -> 422
```

Kết quả probe (tất cả OBSERVED): normal `200` · `q="  "` **422** · `q=""` **422**
· `q` thiếu **422** · `min>max` **422** · `page=0` **422** · `page_size=101`
**422** · `/openapi.json` **200** · `/docs` **200**.

**Không** đặt `model_config = {"extra": "forbid"}`. OBSERVED: bật lên thì param
lạ (`?bogus=x`) trả 422 — hành vi này **không nằm trong hợp đồng đã khoá**,
thêm vào là mở rộng scope lén. Để mặc định: param lạ bị bỏ qua, trả `200`.

**D-B — KHÔNG cài project bằng `pip install -e .`; chỉ cài 5 dependency đã ghim.**

Bản kế hoạch trước cho `uv pip install -e ".[dev]"` với một `pyproject.toml`
không có `[build-system]` / `[tool.setuptools]`. **Đã dựng đúng cấu hình đó và
chạy thật** — build **bị từ chối**:

```
error: Multiple top-level packages discovered in a flat-layout:
       ['app', 'data', 'plans', 'harness'].
```

setuptools thấy 4 thư mục top-level ở repo root và không tự đoán được cái nào là
package. Phase 1 chết tại chỗ, **không phase nào sau đó chạy được**.

Cách đúng: cài thẳng 5 dependency, **không** cài chính project. Requirement
`pythonpath = ["."]` của phase 1 tồn tại **chính là** để `import app.*` chạy
được mà không cần cài package — `-e .` mâu thuẫn với nó, không phải bổ sung.
Không thêm `[build-system]` vì repo này **không** phải package phân phối; nó là
một ứng dụng chạy tại chỗ.

**D-C — `resolve_category(category_map, value)` sở hữu việc fold ĐẦU VÀO.**

`build_category_map` sinh key **đã fold**. Bản kế hoạch trước không nói ai fold
**giá trị người dùng gửi lên**, nên tra thẳng `category_map.get(value)` sẽ hỏng
**3/7** case đã liệt kê: `Tủ Lạnh`, `TU LANH`, `Máy in` đều miss (key là
`tu lanh` / `may in`), trong khi `refrigerator`, `tu lanh`, `may in`, `monitor`
trúng. Đưa việc fold đầu vào vào một hàm có tên rõ ràng ở `app/loader.py`.

**D-D — Route PHẢI ghim `response_model=`.**

`search.run()` trả `list[dict]` thô. Nếu route không ghim `response_model`,
FastAPI serialize thẳng dict và `ProductOut` **có thể được viết hoàn hảo mà
không bao giờ được nối vào** — không test nào hiện có bắt được.

OBSERVED trên sản phẩm thật: dict thô có **18** trường, `ProductOut` có **13**;
rò rỉ đúng **5** trường — `availability`, `image_url`, `model_code`,
`product_id_web`, **`specifications`** — và payload phình **2.24x** (1177 vs 525
byte). `specifications` bị plan tuyên bố out-of-scope **ba** lần.

Ghim `response_model=PaginationEnvelope` cho `/products` + `/products/search`,
`response_model=ProductOut` cho `/products/{product_id}`.

## Ràng buộc (constraint-scan)

| Nguồn | Ràng buộc | Ảnh hưởng |
|---|---|---|
| `harness/data/ownership.yaml:18` | zone `plans: [plans/]` | Plan chỉ ghi trong `plans/`. Code sinh ra ở `app/`, `tests/` không thuộc zone nào → không bị fs_guard chặn |
| `harness/data/stage-policy.yaml:27-38` | `push` → `requires: [verification]` | Mỗi phase phải emit `verification-P<n>.json`; xem `post:` trong `plan-graph.yaml` |
| `harness/data/stage-policy.yaml:41-52` | `pr`/`merge`/`ship` → `requires: [verification, review-decision, plan-approval]` | Trước khi mở PR phải có `review-decision.json` + plan được người duyệt |
| `harness/data/stage-policy.yaml:24` | `hard_stage_advisory: true` | Local chỉ **nag**, không chặn. Enforcement thật là remote receipts-gate |
| `docs/code-standards.md:23-40` | Project layout cố định | `app/{main,models,loader,text,index,search}.py` + `tests/{unit,api}/`. Không tự chế layout khác |
| `docs/code-standards.md:42` | Fold sống ở **đúng một chỗ** (`app/text.py`) | Nhân bản = lỗi review chặn merge |
| `docs/code-standards.md:102-104` | Review chặn merge khi: fold bị nhân bản · thứ tự route đảo · test API chỉ assert status · xoá/làm yếu 1 trong 4 test hồi quy · runtime ghi vào JSON | Là gate cứng của phase 5/6 |
| `docs/glossary.yaml` | 4 thuật ngữ đã đăng ký + từ **cấm** | Xem bảng dưới |
| `harness/LESSONS.md` | Chỉ có template, **0 entry** | Không có failure mode cũ nào áp vào plan này |

### Từ vựng bắt buộc (`docs/glossary.yaml`)

| Dùng | **CẤM** |
|---|---|
| `accent fold` | ~~normalize~~, ~~slugify~~ |
| `token index` | ~~search index~~, ~~inverted index~~ |
| `pagination envelope` | ~~wrapper~~, ~~result set~~ |
| `effective price` | ~~final price~~, ~~real price~~ |

Áp cho tên hàm, tên biến, docstring, README, tên test.

### Bẫy đặt tên (`docs/code-standards.md:18-21`)

`data/products.json` **có sẵn key `total` = 20** (tổng bản ghi trong file),
trùng tên `total` của pagination envelope (= số bản ghi khớp sau lọc, trước cắt
trang). **Không được** truyền thẳng `d["total"]` vào response. Loader đọc
`len(d["products"])`, bỏ qua `d["total"]`.

## Phases

| # | Theme | Phụ thuộc | Cỡ | Deliverable |
|---|---|---|---|---|
| 1 | Setup | — | S | `pyproject.toml` deps ghim, venv `uv`, pytest config, khung `app/` + `tests/` |
| 2 | Text Fold | 1 | S | `app/text.py` — accent fold đ-aware + tokenizer. Thuần túy |
| 3 | Loader Models | 1, **2** | M | `app/loader.py` (envelope + fail-fast) + `app/models.py` (Pydantic) |
| 4 | Search Core | 2, 3 | L | `app/index.py` + `app/search.py` — token index, AND, lọc, sort, phân trang |
| 5 | Api Routes | 4 | L | `app/main.py` — app, lifespan, 4 route đúng thứ tự, validator `q` |
| 6 | Docker Docs | 5 | M | `Dockerfile`, `README.md`, xác minh `/docs` lên thật |

**Chuỗi hoàn toàn tuyến tính — không có antichain.** Bản trước khẳng định P2 ⊥ P3;
red-team **H4** chứng minh sai: `app/loader.py` (P3) gọi `app.text.fold` (P2 sở hữu)
ở `build_category_map` và `resolve_category`, nên P2 là tiền đề **cứng** của P3.
Batch dẫn xuất: `[[P1],[P2],[P3],[P4],[P5],[P6]]` (xem Dependency graph + VL-14).

### Ma trận test — 4 test hồi quy bắt buộc nằm ở đâu

`docs/code-standards.md:51-65` bắt buộc 4 test hồi quy + 1 test pagination.
Bảng dưới chốt **tên** và **phase** cho từng cái. Mỗi test hồi quy xuất hiện ở
**hai tầng**: unit (thuần túy) và API (qua HTTP) — vì hỏng ở tầng nào cũng chết.

| # | Khoá chế độ hỏng | Unit (phase) | API (phase) | Số đo OBSERVED |
|---|---|---|---|---|
| R1 | Fold `đ` | `test_fold_maps_d_stroke_to_d` + `test_no_d_stroke_survives_fold_across_dataset` (P2) · `test_search_ngan_da_returns_3` (P4) | `test_api_search_ngan_da_returns_3_ids` (P5) | naive NFD **0 hit** / đ-aware **3 hit**; NFD sót `đ` **8/20**, đ-aware **0/20** |
| R2 | Token nguyên vẹn | `test_search_may_in_returns_exactly_4` (P4) | `test_api_search_may_in_returns_exactly_4` (P5) | substring **16**, whole-token **4** |
| R3 | Assert thân phản hồi, không chỉ status | — | `test_api_search_route_precedence_body_not_status` (P5) | sai thứ tự → `200` `{"hit":"by_id","product_id":"search"}` |
| R4 | Luật giá null | `test_null_price_visible_in_list` + `test_null_price_excluded_by_min_price` + `test_null_price_excluded_by_max_price` (P4) | `test_api_printer_318469_visible_then_hidden` (P5) | list **20** (có `printer-318469`) / `min_price=0` **19** (không có) |
| R5 | `total` ≠ `len(items)` | `test_pagination_total_before_slicing` (P4) | `test_api_page_size_10_of_20` (P5) | `page_size=10` trên 20 → `total=20`, `len(items)=10` |

Xoá hoặc làm yếu bất kỳ dòng nào = review chặn merge
(`docs/code-standards.md:102-104`).

### Test thêm từ red-team — mỗi cái khoá một chỗ hai implementation cùng PASS

Năm dòng dưới đây **không** thuộc 4 test bắt buộc của `docs/code-standards.md`,
nhưng mỗi cái bịt một lỗ nơi bộ test cũ **không phân biệt được** đúng và sai.

| # | Khoá | Phase | Bằng chứng OBSERVED |
|---|---|---|---|
| X1 | `?category=` rỗng → `total == 0` (không phải 20) | P4 unit + P5 API | `?category=` bind thành `""`: `is_none=False`, `truthy=False`. `if category:` → **20**; `is not None` → **0**. Hai bản đều pass toàn bộ test cũ |
| X2 | Biên giá inclusive: `min_price=3290000` → **19** | P4 | `>=` → **19**, `>` → **17**. Test cũ `min_price=0` cho **19** dưới **cả hai** → không phân biệt được |
| X3 | Response **không** rò trường: `"specifications" not in items[0]` | P5 | dict thô **18** trường vs `ProductOut` **13**; rò 5 trường, payload **2.24x** |
| X4 | `/health` → `products_loaded == 20` | P5 | `len(index)` = **90**, `len(products)` = **20**. Text cũ nói "`len(index)` thật, phải = 20" — sai số học |
| X5 | `q` chỉ dấu câu (`"---"`) → **200**, `total == 0` | P5 | `tokenize("---")` = `set()`. Qua được validator non-blank, nên phải định nghĩa hành vi |

## Out of scope

Kế thừa brief §8, không đổi:

- **Không** frontend / UI — API thuần, JSON in/out.
- **Không** authentication / authorization.
- **Không** database ngoài — nạp JSON vào RAM.
- **Không** cloud deployment — Dockerfile chỉ để chạy local tái lập được.
- **Không** fuzzy search / typo-tolerance (Levenshtein, n-gram).
- **Không** lọc theo `availability` — OBSERVED `"unknown"` **20/20**, không làm được.
- **Không** hỗ trợ ảnh — OBSERVED `image_url` null **20/20**.
- **Không** write API — runtime **không bao giờ** sửa `data/products.json`.
- **Không** lọc theo `specifications` (43 key rời rạc).
- **Không** sort tùy chọn theo giá / % giảm — cố định `id` tăng dần.

Thêm, chốt trong phiên này:

- **Không** từ chối query param lạ (`extra: forbid` tắt) — không nằm trong hợp đồng.
- **Không** ranking / scoring kết quả — thứ tự là `id` tăng dần, hết.
- **Không** CI pipeline / GitHub Actions — ngoài phạm vi đợt này.
- **Không** đo coverage threshold — gate là 100% test pass, không phải % dòng.

_(Cái gì không liệt kê là chưa quyết, không phải đã duyệt.)_

## Tương thích ngược & migration

Greenfield: **0 consumer hiện có, 0 dữ liệu cần migrate, 0 integration cần giữ**.
Repo chưa có code (OBSERVED). Không có contract-delta để ghi.

Thứ được coi là hợp đồng cần giữ ổn định **từ đây trở đi**:

1. Hình dạng pagination envelope `{total, page, page_size, items}`.
2. Thứ tự `id` tăng dần — chính là lý do chốt nó thay vì thứ tự file. OBSERVED:
   thứ tự file bắt đầu `refrigerator-358160`, sort id bắt đầu
   `air-conditioner-335837`. Nếu `data/products.json` được sinh lại với thứ tự
   khác, contract sort-theo-`id` **không đổi**; contract theo-thứ-tự-file sẽ đổi
   nội dung từng trang mà không test nào đỏ.
3. Mã lỗi: 422 cho đầu vào sai, 404 cho `product_id` không tồn tại.

Đổi bất kỳ điểm nào trong ba điểm trên sau khi phase 6 xong = contract-delta,
phải ghi 4 trường (before / after / who-affected / migration-path) theo
`harness/rules/scope-and-contract-discipline.md`.

## Acceptance (toàn plan)

Phân loại theo `harness/rules/testability-triad.md`: **test** / **invariant** /
**manual**.

| # | Tiêu chí | Loại |
|---|---|---|
| A1 | `"ngan da"` trả **đúng 3** sản phẩm: `refrigerator-358160`, `refrigerator-363108`, `refrigerator-363109` | **test** |
| A2 | `"may in"` trả **đúng 4**: `printer-318468`, `printer-318469`, `printer-357980`, `printer-357982` — không phải 16 | **test** |
| A3 | `GET /products/search?q=...` assert **thân phản hồi**; body **không bao giờ** chứa key `product_id` ở tầng ngoài | **test** |
| A4 | `printer-318469` **có** trong `GET /products` (total 20); **không có** khi `min_price=0` (total 19) | **test** |
| A5 | `GET /products?page_size=10` → `total=20` **và** `len(items)=10` | **test** |
| A6 | `q` rỗng / chỉ khoảng trắng / thiếu → **422**; `min_price>max_price` → **422**; `page=0` → **422**; `page_size=101` → **422** | **test** |
| A7 | `GET /products/{id}` không tồn tại → **404** với thân `{"detail": ...}` | **test** |
| A8 | `category` nhận cả `refrigerator`, `tu lanh`, `Tủ Lạnh`, `TU LANH` → cùng **4** kết quả | **test** |
| A9 | `page=3&page_size=10` trên 20 → `items: []`, `total: 20`, **không** 404 | **test** |
| A10 | Fold **chỉ có một** implementation: `test_loader_uses_text_fold_not_local_copy` + `test_search_uses_text_fold_not_local_copy` assert `app.loader.fold is app.text.fold` **và** `app.search.fold is app.text.fold` (so sánh **identity đối tượng hàm**, không phải grep) | **test** |
| A11 | `grep -rn "fastapi\|starlette" app/` chỉ khớp `app/main.py` — tầng thuần túy không biết HTTP (`docs/code-standards.md:14-15`) | **invariant** |
| A12 | `test_api_dataset_file_unchanged`: ghi `mtime` + `st_size` + SHA-256 của `data/products.json` trước, chạy loạt request, assert cả ba **không đổi** | **test** |
| A13 | Route ghim `response_model=` (D-D): `PaginationEnvelope` cho `/products` + `/products/search`, `ProductOut` cho `/products/{id}`. Test assert `"specifications" not in items[0]` | **test** |
| A14 | `grep -rniE "\b(normalize\|slugify\|inverted.index\|search.index\|wrapper\|result.set\|final.price\|real.price)\b" app/ README.md` → **0 dòng**. Ngoại lệ duy nhất được phép: lời gọi stdlib `unicodedata.normalize` trong `app/text.py` (loại bằng `grep -v "unicodedata.normalize"`) | **invariant** |
| A15 | `uvicorn app.main:app` lên thật, `http://127.0.0.1:8000/docs` render UI trong trình duyệt; container Docker cũng vậy — cần mắt người, `manual_test_anchor.py` | **manual** |
| A16 | Mỗi phase red→green TDD; `.venv/bin/python -m pytest tests/ -q` xanh **100%** sau mỗi phase, không skip / không `xfail` | **invariant** |
| A17 | `?category=` → `total == 0`; `?brand=` → `total == 0` (quyết định #15 — **không** phải 20) | **test** |
| A18 | `min_price=3290000` → `total == 19` (biên inclusive, quyết định #16) | **test** |
| A19 | `GET /health` → `products_loaded == 20` (quyết định #17) | **test** |

**A10 và A12 đã bị viết lại.** Bản trước dùng grep và **cả hai đều không phải
guard thật** — đo trực tiếp: A12 regex cũ khớp **0/3** dạng ghi thực tế
(`p.write_text(...)`, `open(DATA_PATH,"w")` với biến, `open(path,"w").write(x)`);
A10 cũ chỉ ghim **nơi `unicodedata` được import**, nên một bản fold nhân bản viết
bằng `str.maketrans` lách qua với **0** hit. Hai tiêu chí đó đang **đóng vai**
guard trong bảng nghiệm thu mà không chặn được gì. Guard thật là test identity
và test mtime/size/SHA — đều đã sống sót qua đợt tấn công.

## Rollback

Mỗi phase = **một commit riêng** (conventional commit,
`docs/code-standards.md:96`). Nhánh `feat/product-search-api`, không commit
thẳng `main`.

| Phase | Cách hoàn tác | Thiệt hại lan truyền |
|---|---|---|
| 1 | `git revert <sha>` + `rm -rf .venv` | Không — chưa ai phụ thuộc |
| 2 | `git revert <sha>` | P4 mất `fold`/`tokenize` → revert P4 trước |
| 3 | `git revert <sha>` | P4 mất `load_products` → revert P4 trước |
| 4 | `git revert <sha>` | P5 mất `search.run` → revert P5 trước |
| 5 | `git revert <sha>` | P6 README/`/docs` claim sai → revert P6 trước |
| 6 | `git revert <sha>` | Không — chỉ Dockerfile + README |

Thứ tự revert **ngược** thứ tự phase. Sau mỗi revert chạy
`python3 -m pytest tests/ -q` — phải xanh trước khi làm tiếp.

Rollback toàn bộ: `git reset --hard b826abf` (commit trước khi plan bắt đầu,
OBSERVED trong `git log`) — nhánh feature, an toàn.

## Risks

Thang C/H/M/L (Critical / High / Medium / Low).

| # | Rủi ro | Khả năng | Tác động | Mitigation |
|---|---|---|---|---|
| RK1 | **Fold hồi quy về NFD ngây thơ** khi refactor | **H** | **C** — hỏng thầm lặng 8/20, không lỗi nào bắn | R1 ở cả P2 và P5. Test quan trọng nhất bộ test. Chống nhân bản bằng **test identity** `app.loader.fold is app.text.fold` + `app.search.fold is app.text.fold` (A10 đã viết lại — grep `unicodedata` cũ **không** chặn được bản nhân bản dùng `str.maketrans`, đo được **0** hit) |
| RK2 | **Route `{product_id}` đặt trước `search`** | **H** | **C** — trả **200** `{"hit":"by_id","product_id":"search"}`, test assert status-only sẽ **PASS trong khi API hỏng** | R3 assert thân phản hồi. P5 khai báo `search` trước, có comment cảnh báo tại chỗ |
| RK3 | **`TestClient(app)` không dùng context manager → `lifespan` không chạy** ⟵ MỚI | **H** | **C** — index rỗng, `GET /products` trả `200 {"total": 0, "items": []}`. Mọi assert đếm sẽ sai mà không có lỗi nào | Fixture `client` trong `tests/api/conftest.py` **bắt buộc** `with TestClient(app) as c: yield c`. P5 có test khẳng định `total == 20` — bẫy này làm nó về 0 ngay |
| RK4 | **`Query(min_length=1)` KHÔNG chặn `q` toàn khoảng trắng** ⟵ MỚI | **H** | **H** — `?q=%20%20` trả **200** thay vì 422, vi phạm quyết định #4 | `field_validator("q")` raise `ValueError` khi `not v.strip()`. OBSERVED: `min_length` một mình → 200; thêm validator → 422 |
| RK5 | Cài `httpx` thay vì **`httpx2`** | **H** | **H** — `TestClient` ném `RuntimeError`, chặn TOÀN BỘ test API | P1 ghim `httpx2==2.7.0` vào dev-deps ngay từ đầu. Anchor: `starlette/testclient.py:33` `import httpx2 as httpx` |
| RK6 | Ai đó "đơn giản hoá" khớp token → substring | **M** | **H** — F1 tụt 0.91 → 0.71, `"may in"` trả **16** thay vì 4 | R2 ở cả P4 và P5 |
| RK7 | `total` bị trả nhầm thành `len(items)` | **M** | **M** — pagination sai âm thầm | R5. Đếm `total` **trước** khi cắt trang, có test riêng |
| RK8 | Đọc nhầm `d["total"]` (=20 có sẵn trong file) làm `total` của envelope | **M** | **M** — đúng tình cờ khi không lọc, **sai** ngay khi lọc | P3 loader bỏ qua `d["total"]`, dùng `len(d["products"])`. P4 có test `category=refrigerator` → `total=4` (không phải 20) |
| RK9 | Coi top-level JSON là **list** thay vì **envelope dict** | **M** | **H** — `TypeError` lúc khởi động, hoặc tệ hơn: index rỗng | OBSERVED: top-level là `dict`, keys `[schema_version, source_file, source_note, currency, total, categories, products]`. P3 fail-fast + test envelope |
| RK10 | Trộn param thường với Pydantic query model trong cùng endpoint ⟵ MỚI | **M** | **H** — **mọi** request trả 422 `{"loc":["query","f"]}`, kể cả hợp lệ | D-A: `q` nằm trong `SearchParams(ListParams)`, không phải param rời. Đã probe xanh |
| RK11 | Va chạm `"tủ"`/`"từ"` bị coi là bug lúc review | **M** | **L** | Trade-off có chủ ý, precision 0.83 / recall 1.00. P4 có test **khoá hành vi**: `"tu lanh"` → **đúng 8** (4 tủ lạnh + 4 máy lạnh) |
| RK12 | `printer-318469` bị coi là mất dữ liệu | **L** | **L** | Có chủ ý theo quyết định #5. R4 khẳng định hiện-khi-list / ẩn-khi-lọc-giá |
| RK13 | Khởi động lên được với index rỗng (JSON hỏng/thiếu) | **L** | **C** — API chạy nhưng trả 0 kết quả cho mọi truy vấn; tệ hơn crash (`docs/code-standards.md:78-79`) | P3 loader **fail fast**: raise khi thiếu key `products` hoặc list rỗng. P5 lifespan không nuốt lỗi |
| RK14 | `pydantic` trong venv (2.13.4) khác bản hệ thống (2.13.2) | **L** | **L** | OBSERVED qua `uv pip compile`. Cả hai đều v2 API. P1 ghim rõ trong `pyproject.toml`, luôn chạy test trong venv |
| RK15 | **`pip install -e .` chết vì flat-layout** ⟵ red-team C1 | **C** (chắc chắn xảy ra) | **C** — phase 1 chết, **không phase nào sau đó chạy** | **Đã chạy thật, build bị từ chối**: `Multiple top-level packages discovered in a flat-layout: ['app','data','plans','harness']`. D-B: bỏ `-e .`, cài thẳng 5 dep đã ghim. Lan sang Dockerfile ở P6 |
| RK16 | **Bộ lọc dùng truthiness thay `is not None`** ⟵ red-team H1 | **H** | **H** — `?category=` trả **20** thay vì 0; **cả hai** bản đều pass 100% test cũ | Quyết định #15 ghim `is not None`. X1 test `?category=` và `?brand=` → `total == 0`. Test `category=bogus` cũ **không** bắt được — chuỗi rỗng đi thẳng qua |
| RK17 | **`ProductOut` được viết nhưng không bao giờ nối vào** ⟵ red-team H2 | **H** | **H** — rò 5 trường gồm `specifications` (out-of-scope 3 lần), payload **2.24x** | D-D ghim `response_model=`. X3 assert `"specifications" not in items[0]` |
| RK18 | **Không ai fold ĐẦU VÀO của `category`** ⟵ red-team H3 | **H** | **M** — 3/7 case đã liệt kê không thể pass | D-C: `resolve_category()` sở hữu việc fold đầu vào, đặt ở `app/loader.py` |
| RK19 | **Biên giá `>` vs `>=` không được ghim, và test cũ không phân biệt được** ⟵ red-team M3 | **M** | **M** — lệch 2 sản phẩm ở biên | Quyết định #16 ghim inclusive. X2: `min_price=3290000` → **19** (`>=`) vs **17** (`>`). Test cũ `min_price=0` cho **19** dưới cả hai |
| RK20 | **Guard giả** — A10/A12 bản cũ trông như guard nhưng không chặn gì ⟵ red-team M1/M2 | **M** | **M** — an toàn giả, tệ hơn không có guard vì nó chặn việc dựng guard thật | Đo trực tiếp: A12 regex khớp **0/3** dạng ghi thực tế; A10 bị `str.maketrans` lách (**0** hit). Cả hai đã viết lại thành test thật |

## Môi trường — trạng thái OBSERVED lúc lập kế hoạch

Kiểm chứng lại trực tiếp trong phiên này, **không** chép từ brief.

| Gói | Trạng thái hệ thống | Ghi chú |
|---|---|---|
| `fastapi` | **MISSING** | P1 là việc thật, không phải thủ tục |
| `uvicorn` | **MISSING** | |
| `starlette` | **MISSING** | |
| `httpx2` | **MISSING** | |
| `httpx` | PRESENT 0.28.1 | **SAI GÓI** cho `TestClient` |
| `pydantic` | PRESENT 2.13.2 | |
| `pytest` | PRESENT 9.1.1 | |
| `uv` | `/home/dholmes/.local/bin/uv` | |

Repo **không có** `pyproject.toml`, `requirements.txt`, `app/`, `tests/`.

`uv pip compile` (chạy thật, phiên này) chốt bản ghim cho P1:

```
fastapi==0.139.2   starlette==1.3.1   uvicorn==0.51.0
pydantic==2.13.4   pydantic-core==2.46.4
pytest==9.1.1      httpx2==2.7.0
anyio==4.14.2      h11==0.16.0        typing-extensions==4.16.0
```

## Validation Log

| ID | Vấn đề | Xử lý |
|---|---|---|
| VL-1 | Brief §6 để mở 4 câu hỏi | Người dùng chốt cả 4 trong phiên này → quyết định #10-#14 |
| VL-2 | Brief **không hề nhắc** top-level JSON là envelope dict | Probe lại: đúng là dict, sản phẩm ở `d["products"]` → RK9 + P3 |
| VL-3 | Bằng chứng discovery về 3 failure mode là probe ephemeral; `fastapi` giờ MISSING → OBSERVED đã suy giảm thành PRIOR | Dựng venv tạm, chạy lại cả 3. **Cả 3 tái lập chính xác.** Trở lại OBSERVED |
| VL-4 | Quyết định #12 (`min>max` → 422) cần validation liên trường, `Query()` rời không làm được | Probe `model_validator` → 422 native. Chốt D-A |
| VL-5 | Thiết kế đầu của D-A trộn `q` rời + query model → **mọi** request 422 | Probe bắt được. Đổi sang `SearchParams(ListParams)`, probe lại xanh → RK10 |
| VL-6 | `extra: "forbid"` làm param lạ trả 422 | Không nằm trong hợp đồng khoá → **tắt**, ghi vào Out of scope |
| VL-7 | `decisions: []` trong frontmatter trống dù `DEC-1` chi phối plan | **ĐÃ XỬ** — main sửa thành `decisions: [DEC-1]` sau khi planner bàn giao; sau đó mở rộng thành `[DEC-1, DEC-2, DEC-3]` ở VL-31 |
| VL-26 | **Người dùng bắt lỗi**: `/health` vẫn còn một hàng hợp đồng cũ ghi `products_loaded = len(index)` ở cuối plan.md, dù VL-13 đã sửa các chỗ khác | Sửa nốt hàng đó. `products_loaded` = `len(dataset.products)` = **20** ở **mọi** chỗ trong plan.md và P5. `len(index)` = 90 chỉ còn xuất hiện như phản-ví-dụ có nhãn |
| VL-27 | **Người dùng bắt lỗi**: `README.md` **đã tồn tại** (105 byte, đã commit) nhưng P6 xếp nó vào **Create** và RED mong `FileNotFoundError` | **Xác minh: đúng.** `ls` + đọc nội dung: có sẵn tiêu đề `# SDLC Harness Internship`. Chuyển sang **Modify** ở P6/plan.md/plan-graph.yaml; RED giờ đỏ vì `assert len(pairs) == 5` được 0 cặp. Cả tôi lẫn red-team đều **bỏ sót** cái này |
| VL-28 | **Người dùng bắt lỗi**: P6 dùng `docker run` **foreground** rồi bảo chạy `curl` ở bước sau — treo terminal, không bao giờ tới bước curl | Chuyển sang `docker run -d --rm --name product-search-api-demo`, thêm vòng poll `/health` (30×0.5s) trước khi curl, và bước `docker stop` **bắt buộc** để trả cổng 8000 |
| VL-29 | **Người dùng bắt lỗi**: P2 ghi "Độc lập với phase 3" — mâu thuẫn với H4 | Viết lại thành quan hệ **một chiều**: P2 không phụ thuộc P3; **P3 phụ thuộc P2** |
| VL-30 | **Người dùng bắt lỗi**: câu "rủi ro tụ ở đúng **ba** chỗ" đóng cứng con số, trong khi plan giờ ghi nhận **năm** chế độ hỏng-trả-200 | Viết lại theo **luật, không theo số đếm**: "mọi test API phải assert thân phản hồi", danh sách để **mở**. Sửa ở plan.md Tổng quan + P5 Overview |
| VL-31 | **Người dùng nêu**: đừng gọi D-B/D-C là DEC-2/DEC-3 nếu chưa đăng ký | **Tiền đề đã cũ** — `DEC-2`/`DEC-3` **đã được đăng ký thật** trong phiên này qua `decision_register.py` (xác minh: `docs/decisions.md` có `## DEC-1/2/3`). Nhưng lo ngại gốc là đúng: hai hệ đánh số đang lẫn. Thêm bảng ánh xạ (D-B↔DEC-2, D-C⊂DEC-3, D-A/D-D **không** phải DEC) + frontmatter thành `[DEC-1, DEC-2, DEC-3]` |
| VL-8 | Câu hỏi mở #2 (`/health` chưa có hợp đồng thân) | **CHỐT**: `{"status": "ok", "products_loaded": 20}`. Số sản phẩm là canary bắt RK3/RK13 ngay ở endpoint rẻ nhất |
| VL-9 | Câu hỏi mở #3 (`requires-python`) | **CHỐT** `>=3.13`. Máy OBSERVED 3.13.13; không có nhu cầu chạy bản thấp hơn trong scope local-only |
| VL-10 | Câu hỏi mở #4 (base image Docker) `[PRIOR]` | **CHỐT** `python:3.13-slim` — **pull thật**, không còn giả định. `docker` tại `/usr/bin/docker`, daemon chạy; image 43 MB, digest `sha256:6771159c…`, Python **3.13.14** bên trong. `[PRIOR]` → **OBSERVED**, fallback bị bỏ |
| VL-11 | red-team **C1**: `pip install -e .` + flat-layout → phase 1 chết | **Tái lập thật** trong dir mô phỏng layout repo: build bị từ chối, `Multiple top-level packages … ['app','data','plans','harness']`. Chốt **D-B** (bỏ `-e .`, cài 5 dep ghim). Lan sang P1 bước cài + P6 Dockerfile → RK15 |
| VL-12 | red-team **H1**: `?category=` là silent-200 thứ TƯ | **Probe thật**: `?category=` → `""`, `is_none=False`, `truthy=False`. `if category:` → 20; `is not None` → 0. Chốt **quyết định #15** + X1 → RK16. Ghi chú thêm: `?category=%20` (một dấu cách) bind thành `" "` — **truthy**, nên `is not None` xử lý nhất quán cả hai, truthiness thì không |
| VL-13 | red-team **H5**: `/health` sai số học | **Đo thật**: `len(index)` = **90**, `len(products)` = **20**. Text cũ ("`len(index)` thật, phải = 20") khiến implementation trả 90 rồi P5 assert 20 và đỏ **sai lý do**. Chốt **quyết định #17** → sửa plan.md, P5, P6 |
| VL-14 | red-team **H4**: P2 ⊥ P3 là **sai** | `app/loader.py` gọi `app.text.fold` (P2 sở hữu) ở 3 chỗ. Thêm cạnh `{from: P2, to: P3}`; batch dẫn xuất `[[P1],[P2],[P3],[P4],[P5],[P6]]`. Không hỏng gì hôm nay (chạy tuần tự) nhưng sidecar máy-đọc đang khẳng định antichain không tồn tại |
| VL-15 | red-team **M3**: biên giá không ghim, test cũ không phân biệt được | **Đếm thật**: `min_price=3290000` → **19** (`>=`) / **17** (`>`); `min_price=0` → **19** dưới **cả hai**. Red-team đoán 18 — **sai**, số thật là **17**. Chốt **quyết định #16** + X2 → RK19 |
| VL-16 | red-team **M1/M2**: A10 + A12 là guard giả | **Đo thật**: A12 regex cũ khớp **0/3** dạng ghi thực tế; bản fold nhân bản bằng `str.maketrans` cho **0** hit `unicodedata`. Viết lại cả hai thành test thật (identity + mtime/size/SHA) → RK20 |
| VL-17 | red-team **H2**: `ProductOut` có thể không bao giờ được nối vào | **Đo thật**: dict thô **18** trường vs `ProductOut` **13**; rò `availability, image_url, model_code, product_id_web, specifications`; payload **2.24x** (1177→525 byte). Chốt **D-D** (`response_model=`) + X3 → RK17 |
| VL-18 | red-team **H3**: không ai fold đầu vào `category` | Tra thẳng `category_map.get(value)` hỏng **3/7** case đã liệt kê (`Tủ Lạnh`, `TU LANH`, `Máy in`). Chốt **D-C**: `resolve_category()` ở `app/loader.py` → RK18 |
| VL-19 | red-team **M5**: chữ ký `load_dataset` mâu thuẫn giữa P3 và P5 | P3 ghi `load_dataset(path: Path)`, P5 gọi `load_dataset()`. Chốt default ghim tuyệt đối: `Path(__file__).resolve().parent.parent / "data" / "products.json"` — không phụ thuộc cwd |
| VL-20 | red-team **M6/M7**: test RED của P1 không thật RED, và chạy trước khi có venv | `test_dataset_is_envelope_with_20_products` chỉ đọc file có sẵn → pass với **0** dòng implementation. Chuyển sang Tests After. Bước RED chạy bằng `python3` hệ thống (venv chưa tồn tại) — ghi rõ trong P1 |
| VL-21 | red-team **M4**: test README-vs-thực-tế pass rỗng | Regex parse **0** cặp → vòng lặp rỗng → pass vô nghĩa; nó là guard **duy nhất** chống số bịa trong README. Thêm `assert len(pairs) == 5` trước vòng lặp. Bảng curl có **9** dòng nhưng chỉ **5** dòng mang `total:` → sửa claim "9/9" thành **5/9** |
| VL-22 | red-team **L1/L5**: `q` chỉ dấu câu chưa định nghĩa; A14 không có lệnh kiểm | `tokenize("---")` = `set()` → chốt **200** + `total == 0` (X5). A14 được cấp lệnh grep thật kèm ngoại lệ `unicodedata.normalize` |
| VL-24 | red-team **H6**: grep kiểm thứ tự route khớp chính **comment cảnh báo** mà plan bắt buộc phải có | Grep chuỗi trần `"products/search"` pass **kể cả khi route bị đảo**. Đổi sang grep decorator đầy đủ `@app.get("/products/search")` — comment không lọt vào được |
| VL-25 | red-team **M9**: `tests/unit/test_smoke.py` thiếu trong bảng file-ownership của plan.md trong khi ngay dưới đó khẳng định "Zero overlap" | Thêm vào hàng phase 1. Bảng giờ khớp `plan-graph.yaml` **6/6** node |
| VL-23 | red-team **M8**: nhánh fallback giá là **nhánh chết** trên dữ liệu thật | **Đếm thật**: **0** sản phẩm có (`effective_price` null **và** `original_price` có); 19 có `effective_price`, 1 null cả hai. `test_effective_price_falls_back_to_original` **không thể** chạm nhánh này bằng dữ liệu thật → P3 ghi rõ test dùng **dict tổng hợp**, kèm lý do trong docstring |

## Câu hỏi còn mở

**Không còn.** Cả bốn câu hỏi planner nêu đã được chốt ở main trước khi red-team
(xem VL-7 … VL-10):

1. ~~`decisions: []` trống~~ → **`decisions: [DEC-1]`**. Đã sửa.
2. ~~`/health` chưa có hợp đồng thân~~ → **`{"status": "ok", "products_loaded": 20}`**.
   Trả kèm số sản phẩm đã nạp để `/health` làm luôn vai trò canary: RK3 (lifespan
   không chạy) và RK13 (khởi động với index rỗng) đều làm số này về `0`, phát
   hiện ngay ở endpoint rẻ nhất thay vì đợi test search đếm sai.
3. ~~Python floor~~ → **`requires-python = ">=3.13"`**. Máy OBSERVED 3.13.13,
   scope local-only, không có nhu cầu bản thấp hơn.
4. ~~Base image `[PRIOR]`~~ → **`python:3.13-slim`**. OBSERVED phiên này:
   `docker` tại `/usr/bin/docker`, **daemon đang chạy**. P6 phải build + run thật
   rồi `curl` vào container — không được dừng ở "Dockerfile đã viết xong".

### Hợp đồng `/health` (chốt)

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `status` | `str` | `"ok"` khi dataset đã nạp |
| `products_loaded` | `int` | **`len(dataset.products)`**, phải = **20** |

**`products_loaded` = `len(dataset.products)`, TUYỆT ĐỐI không phải `len(index)`**
(quyết định #17). OBSERVED: `len(index)` = **90** (số token phân biệt),
`len(dataset.products)` = **20**. Trường tên `products_loaded` mà báo cáo 90 sản
phẩm là sai cả số học lẫn tên gọi; implementation bám theo `len(index)` sẽ khiến
test P5 đỏ **sai lý do** và đẩy người sửa đi chữa nhầm chỗ.

Test P5: `GET /health` → `200` và `products_loaded == 20`. Assert vào **con số**,
không phải `status == "ok"` — `status` là hằng chuỗi, nó xanh kể cả khi index rỗng.
