---
harness_version: 5.3.0
harness_kit_digest: 251ed307796039124b44d71759b3f62d8bb9135c4bf3053156e38798587a50a8
harness_schema_version: 1.0
---

# Discovery Brief — Product Search API

**Date:** 2026-07-19
**Status:** **finalized**
**Branch:** `feat/product-search-api`
**DEC:** `DEC-1` (`docs/decisions.md`)

---

## 1. Problem framing

Repo hiện chỉ có một dataset tĩnh `data/products.json` (20 sản phẩm, 5 category, tiếng Việt có dấu) và **không có dòng code nào**. Cần một HTTP API đọc-only cho phép tìm sản phẩm theo text tiếng Việt **không dấu**, lọc theo category / brand / khoảng giá, phân trang, trả JSON.

Người dùng: chính tác giả và người chấm bài internship, chạy local. Không deploy, không auth, không lo tải.

**Root cause:** greenfield — chưa có gì để search.
**Current impact:** không có; đây là tính năng mới, không phải sửa lỗi.
**Deadline / urgency:** không nêu. Coi như bình thường.

---

## 2. Hard constraints

| Constraint | Type | Notes |
|---|---|---|
| Python + FastAPI | technical | Người dùng chốt. `pydantic 2.13.2`, `pytest 9.1.1` đã có sẵn (OBSERVED) |
| `fastapi` + `uvicorn` chưa cài | technical | OBSERVED: cả hai MISSING trên máy. Probe cài thật bằng `uv` → thành công (`fastapi`, `starlette 1.3.1`, `uvicorn 0.51.0`) |
| **`starlette 1.3.1` cần `httpx2`, KHÔNG phải `httpx`** | technical | **OBSERVED** — xem mục 3, phát hiện #6. `httpx 0.28.1` có sẵn trên máy là **sai gói** cho `TestClient` |
| Python 3.13.13 | technical | OBSERVED |
| Chỉ chạy local | policy | Không deploy, không auth, không rate-limit |
| API thuần, không UI | scope | Người dùng chốt |
| `data/products.json` là nguồn dữ liệu duy nhất, **bất biến lúc runtime** | technical | 28 KB, 20 sản phẩm. Nạp một lần lúc khởi động; runtime không bao giờ ghi |

---

## 3. Evidence summary

**Research report:** `[SKIPPED]` — **cố ý**. `hs:research` không được chạy vì câu hỏi chịu lực ở đây đo được trực tiếp trên chính dataset này. Probe thật cho bằng chứng mạnh hơn tổng hợp tài liệu, và harness ưu tiên OBSERVED hơn `[PRIOR]`. Bảy phát hiện dưới đây **đều đã CHẠY THẬT**.

1. **[OBSERVED] Bỏ dấu kiểu ngây thơ bằng `unicodedata` NFD hỏng trên 8/20 sản phẩm.** `đ` không phải dấu tổ hợp (category `Mn`) nên NFD không đụng tới. `"Ngăn đá"` fold ra `"ngan đa"`, người dùng gõ `"ngan da"` → **0 kết quả, không lỗi**. Bắt buộc map `đ→d` / `Đ→d` **riêng, sau** bước NFD. Sau khi sửa: 0/20 blob còn sót `đ`.

2. **[OBSERVED] Khớp chuỗi con sai về mặt đo được.** Bake-off trên 5 truy vấn có ground-truth (`may in`, `tu lanh`, `may lanh`, `may tinh bang`, `man hinh`), chấm theo category đúng:

   | Chiến lược khớp | Precision | Recall | **F1** | Sai (FP) |
   |---|---|---|---|---|
   | substring (ngây thơ) | 0.56 | 1.00 | 0.71 | 16 |
   | **token nguyên vẹn** ← chọn | **0.83** | **1.00** | **0.91** | **4** |
   | token tiền tố | 0.62 | 1.00 | 0.77 | 12 |

   Cụ thể: `"MAY IN"` (máy in) trả **16/20** sản phẩm dưới substring, vì `"in"` nằm trong `"Inverter"`.

3. **[OBSERVED] Bỏ dấu tiếng Việt tạo va chạm đồng tự không sửa được.** Cả 4 false-positive còn lại của token-nguyên-vẹn đều một nguyên nhân: `"tủ"` và `"từ"` cùng fold thành `"tu"`, nên `"tu lanh"` kéo theo 4 máy lạnh có `"Từ 30 - 40m²"` trong tên. Hệ quả **cố hữu** của bỏ dấu, không phải bug tokenizer.

4. **[OBSERVED] Đúng MỘT sản phẩm null cả hai trường giá: `printer-318469`** (`effective_price=None`, `original_price=None`, `sale_price=None`). Đây là mục tiêu cụ thể, duy nhất của luật giá ở mục 5 — không phải tình huống giả định, và nó thành **test fixture** trực tiếp.

5. **[OBSERVED] Thứ tự file KHÁC thứ tự sort theo `id`.** `id` duy nhất 20/20. File bắt đầu bằng `refrigerator-358160`; sort theo id bắt đầu bằng `air-conditioner-335837`. Hai lựa chọn này **không tương đương** — phải chốt một cái, xem mục 5.

6. **[OBSERVED] `starlette 1.3.1` yêu cầu `httpx2`, không phải `httpx`.** `TestClient` ném `RuntimeError: The starlette.testclient module requires the httpx2 package`. Máy đang có `httpx 0.28.1` — **sai gói**. Đã probe cài `httpx2==2.7.0` → `TestClient` chạy được. Ảnh hưởng trực tiếp tới yêu cầu integration test.

7. **[OBSERVED] Va chạm thứ tự route trả `200`, KHÔNG phải `404` — hỏng thầm lặng.** Probe FastAPI thật, hai app:

   | Thứ tự khai báo | `GET /products/search` trả về |
   |---|---|
   | `{product_id}` trước | `200` `{"hit": "by_id", "product_id": "search"}` ← **sai, nhưng vẫn 200** |
   | `search` trước ← đúng | `200` `{"hit": "search"}` |

   Đây là lý do #9 nguy hiểm: test chỉ assert `status_code == 200` sẽ **PASS trong khi API đã hỏng**. Test bắt buộc phải assert vào **thân phản hồi**.

8. **[OBSERVED] Dữ liệu có lỗ hổng chặn tính năng.** `availability` = `"unknown"` ở **20/20**, `image_url` = `null` ở **20/20**, `sale_price` null ở 14/20. Lọc còn-hàng và trả ảnh **không khả thi**.

9. **[OBSERVED] `specifications` là dict tự do:** 43 key phân biệt trên 20 sản phẩm; key phổ biến nhất chỉ xuất hiện 12 lần. Đã loại khỏi scope.

10. **[OBSERVED] SQLite FTS5 có sẵn** (`sqlite_version 3.51.2`) — nên option B ở mục 4 là lựa chọn thật, không phải giả định.

**Tái lập:** mọi con số sinh ra từ probe chạy trực tiếp trên `data/products.json` và trên FastAPI/starlette thật trong phiên discovery này. Probe là script dùng-một-lần, **không commit** — `hs:plan` phải hóa chúng thành test thật.

---

## 4. Option space

| # | Approach | Pros | Cons | Complexity |
|---|---|---|---|---|
| **A** ← chọn | **Index token trong bộ nhớ** — nạp JSON lúc khởi động, fold bỏ dấu, tách token, dựng `dict[token → set(product_id)]` | Không thêm dependency ngoài FastAPI; 20 sản phẩm tra cứu tức thì; kiểm soát hoàn toàn khâu bỏ dấu (chỗ NFD đã chứng minh là hỏng); dễ test thuần túy | Không sống sót qua restart (không quan trọng — dữ liệu tĩnh); không scale tới hàng triệu (ngoài scope) | **low** |
| B | SQLite FTS5 (`unicode61 remove_diacritics 2`) | Có sẵn (OBSERVED 3.51.2); BM25 ranking miễn phí; scale tốt hơn | Tokenizer dựng sẵn **vẫn hỏng ở `đ`** — vẫn phải fold ở tầng ứng dụng, mất đúng lợi ích chính; thêm schema/migration cho 28 KB dữ liệu tĩnh | medium |
| C | Meilisearch / Elasticsearch | Search hạng production, typo-tolerance thật | Chạy service riêng cho 20 bản ghi local-only. Lố bịch về mức độ | high |
| D | Quét substring ngây thơ | Ít code nhất | **Bị đo là sai**: F1 0.71, `"may in"` → 16/20 | low |

---

## 5. Chosen direction + rationale

**Chọn:** Option **A** — index token trong bộ nhớ, khớp **token nguyên vẹn**, fold bỏ dấu có xử lý `đ` riêng.

**Vì sao:**

1. **Số đo chọn hộ, không phải khẩu vị.** F1 **0.91** vs 0.71 (substring) vs 0.77 (tiền tố), trên chính dataset này. Option D bị loại bằng phép đo.
2. **Option B mất lợi thế khi chạm dữ liệu thật.** Điểm bán hàng của FTS5 là `remove_diacritics` dựng sẵn — nhưng probe cho thấy `đ` vẫn phải xử lý ở tầng ứng dụng. Đã phải tự fold thì SQLite chỉ còn là schema thừa cho 28 KB dữ liệu tĩnh.
3. **Quy mô không đòi hỏi gì hơn.** 20 sản phẩm, local, tĩnh.
4. **Khâu bỏ dấu là phần rủi ro nhất và phải nằm trong tầm kiểm soát.** Nó đã hỏng một lần theo đúng cách ai cũng mắc.

**DEC ghi nhận:** **`DEC-1`** — *Search core: in-memory token index + Vietnamese đ-aware accent folding + exact whole-token AND matching*. File: `docs/decisions.md`. Affects: search core, `GET /products/search`, accent folding utility, tokenizer.

### 5.1 Quyết định đã chốt (nguồn: người dùng, 2026-07-19)

| # | Quyết định | Ghi chú |
|---|---|---|
| 1 | In-memory token index · fold `đ→d` riêng · khớp token nguyên vẹn | → `DEC-1` |
| 2 | Text search **chỉ** index `name` + `brand` + `category_name` | **Không** index `specifications` |
| 3 | Nhiều token → **AND**: mọi token phải có trong tập token đã index | |
| 4 | `GET /products/search` **bắt buộc** `q` không rỗng; rỗng hoặc chỉ khoảng trắng → **422** | `GET /products` để lấy toàn bộ |
| 5 | Lọc giá dùng `effective_price`, fallback `original_price` | Null cả hai → vẫn hiện khi list/search, **bị loại** khi có `min_price`/`max_price` |
| 6 | Pagination bắt buộc | `page` ≥1 mặc định 1; `page_size` ≥1 ≤100 mặc định 10 |
| 7 | Thứ tự kết quả **deterministic** | **Chốt: sort theo `id` tăng dần** — xem 5.2 |
| 8 | 4 endpoint bắt buộc | `/health`, `/products`, `/products/search`, `/products/{product_id}` |
| 9 | Khai báo `/products/search` **trước** `/products/{product_id}` | Bằng chứng #7 — sai thứ tự trả **200**, không phải 404 |

### 5.2 Thứ tự kết quả — chốt `id` tăng dần

Người dùng cho chọn giữa "giữ thứ tự file" và "sort theo `id`". Probe #5 cho thấy **hai cái này khác nhau thật**, nên phải chốt rõ.

**Chọn: `ORDER BY id ASC`**, áp dụng cho cả `/products` lẫn `/products/search`.

**Vì sao:** đây là contract **độc lập với cách file được serialize**. Thứ tự file là ngẫu nhiên theo cách dataset được sinh ra; nếu `data/products.json` có ngày được tạo lại với thứ tự khác, pagination theo thứ tự file sẽ **âm thầm đổi nội dung từng trang** mà không có test nào đỏ. Sort theo `id` là thứ tự khẳng định được, kiểm chứng được, không cần tham chiếu tới layout file. `id` duy nhất 20/20 (OBSERVED) nên thứ tự là toàn phần và ổn định.

**Trade-off chấp nhận:** mất thứ tự nhóm theo category của file gốc — kết quả sẽ bắt đầu bằng `air-conditioner-*` thay vì `refrigerator-*`. Không ảnh hưởng gì vì API không hứa hẹn nhóm theo category.

### 5.3 API contract (đầu vào cho hs:plan)

| Endpoint | Query params | Trả về |
|---|---|---|
| `GET /health` | — | trạng thái service |
| `GET /products` | `category`, `brand`, `min_price`, `max_price`, `page`, `page_size` | phong bì phân trang |
| `GET /products/search` | **`q` (bắt buộc, không rỗng)** + mọi param của `/products` | phong bì phân trang |
| `GET /products/{product_id}` | — | một sản phẩm, **404** nếu không thấy |

**Phong bì phân trang:** `total`, `page`, `page_size`, `items`.
`total` = **tổng số bản ghi khớp sau khi lọc, trước khi cắt trang** (không phải số phần tử trong `items`).

**Trade-off chấp nhận toàn hệ:**

- **Nhận 4 false positive `"tủ"`/`"từ"`** (precision 0.83) thay vì đuổi theo 1.00. Va chạm đồng tự cố hữu; sửa cần xếp hạng theo category hoặc chấm điểm có trọng số — **không tương xứng** với 20 sản phẩm. Recall 1.00: không sản phẩm đúng nào bị bỏ sót.
- **Index dựng lại mỗi lần khởi động.** Không đáng kể ở 28 KB; đổi lại không cần lo invalidation.
- **`"dien"` trả 0 kết quả** dù `"điện"` có trong `specifications` — hệ quả trực tiếp, đã biết và chấp nhận, của quyết định #2.
- **`printer-318469` biến mất khi lọc giá.** Hệ quả trực tiếp của quyết định #5. Có chủ ý, không phải bug.

---

## 6. Open questions

Mọi câu hỏi mở của bản draft đã được người dùng chốt (mục 5.1). Còn lại là chi tiết mức triển khai — `hs:plan` tự quyết, không chặn:

- [ ] `min_price > max_price` → 422 hay trả rỗng? (nghiêng về **422**: lỗi người gọi, nên nói thẳng)
- [ ] `category` / `brand` khớp chính xác hay cũng bỏ dấu + không phân biệt hoa thường? (nghiêng về **không phân biệt hoa thường, dùng chung fold** cho nhất quán với `q`)
- [ ] `page` vượt quá `total` → trả `items: []` kèm `total` đúng, hay 404? (nghiêng về **`items: []`**: trang rỗng là kết quả hợp lệ)
- [ ] Có kèm `data/products.json` vào image Docker hay mount volume? (nghiêng về **COPY vào image** — dữ liệu tĩnh, bất biến, tái lập được)

---

## 7. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Bỏ dấu hồi quy về NFD ngây thơ khi refactor | **high** | high — hỏng thầm lặng 8/20, không lỗi nào bắn ra | Test bắt buộc: `"ngan da"` phải khớp `"Ngăn đá"`. Test quan trọng nhất bộ test |
| Route `{product_id}` bị đặt trước `search` | **high** | high — **trả 200**, test chỉ check status sẽ PASS trong khi API hỏng | Test bắt buộc assert vào **thân phản hồi** của `/products/search`, không chỉ status code (bằng chứng #7) |
| Ai đó "đơn giản hóa" thành substring | medium | high — F1 tụt về 0.71 | Test hồi quy: `"may in"` phải trả **đúng 4**, không phải 16 |
| Integration test cài nhầm `httpx` thay vì `httpx2` | **high** | medium — `TestClient` ném RuntimeError, chặn toàn bộ test API | Ghi `httpx2` vào dev-deps ngay từ đầu (bằng chứng #6) |
| `total` bị trả nhầm thành `len(items)` | medium | medium — pagination sai âm thầm | Test: `page_size=10` trên 20 sản phẩm phải cho `total=20`, `len(items)=10` |
| Va chạm `"tủ"`/`"từ"` bị coi là bug lúc review | medium | low | Đã ghi là trade-off có chủ ý kèm số đo; nên có test khóa hành vi lại |
| `printer-318469` bị coi là mất dữ liệu | low | low | Có chủ ý theo quyết định #5; nên có test khẳng định nó **hiện** khi list và **ẩn** khi lọc giá |

---

## 8. Explicitly OUT of scope

- **Không** frontend / UI — API thuần, JSON in/out.
- **Không** authentication / authorization.
- **Không** database ngoài — dữ liệu nạp từ JSON vào bộ nhớ.
- **Không** cloud deployment — Dockerfile chỉ để chạy local tái lập được.
- **Không** fuzzy search / typo-tolerance (Levenshtein, n-gram).
- **Không** lọc theo `availability` — `"unknown"` ở 20/20, không thể làm.
- **Không** hỗ trợ ảnh — `image_url` null ở 20/20.
- **Không** write API — chỉ đọc; runtime **không bao giờ** sửa `data/products.json`.
- **Không** lọc theo `specifications` (43 key rời rạc, cần chuẩn hóa trước).
- **Không** sort tùy chọn theo giá / % giảm — thứ tự cố định `id` tăng dần (5.2).

_(Mọi thứ không liệt kê ở đây là chưa quyết, không phải đã duyệt.)_

---

## 9. Delivery checklist (trong scope)

- [ ] Pydantic models cho response (sản phẩm, phong bì phân trang, lỗi)
- [ ] pytest unit tests — fold bỏ dấu, tokenizer, logic lọc, biên phân trang
- [ ] pytest API integration tests qua `TestClient` (**cần `httpx2`**, bằng chứng #6)
- [ ] Dockerfile chạy local tái lập được
- [ ] README: setup, test, run, ví dụ `curl`
- [ ] OpenAPI tại `/docs` (FastAPI tự sinh — xác minh nó lên thật)
- [ ] Runtime không ghi vào `data/products.json`

---

## Handoff -> hs:plan

```
/hs:plan --hard --tdd /home/dholmes/VFS_TTS/sdlc-harness-internship/plans/260719-1435-product-search-api/discovery-brief.md
```

Nhớ `/clear` trước để tránh context discovery làm lệch khâu lập kế hoạch
(`harness/rules/workflow-handoffs.md` #5).

**Ghi chú cho planner — bốn phát hiện phải trở thành test thật trong pha TDD.** Chúng là probe dùng-một-lần, **chưa commit**; không hóa thành test thì mất sạch:

1. `đ` hỏng 8/20 dưới NFD ngây thơ → test `"ngan da"` khớp `"Ngăn đá"`
2. substring F1 0.71 vs token 0.91 → test `"may in"` trả **đúng 4**, không phải 16
3. thứ tự route sai trả **200** kèm `product_id="search"` → test assert **thân phản hồi**, không chỉ status
4. `printer-318469` null cả hai trường giá → test nó **hiện** khi list, **ẩn** khi lọc giá
