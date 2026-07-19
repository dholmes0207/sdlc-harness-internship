# Decision Register

---
id: DEC-1
status: active
date: 2026-07-19
actor: "user:duyluuhx07@gmail.com"
ts: "2026-07-19T07:51:01.847073+00:00"
affects: "search core, GET /products/search, accent folding utility, tokenizer"
---

## DEC-1 — Search core: in-memory token index + Vietnamese đ-aware accent folding + exact whole-token AND matching

Chọn bằng phép đo trên chính data/products.json, không bằng lập luận. (1) Bake-off 5 truy vấn ground-truth: token nguyên vẹn F1=0.91 (P=0.83/R=1.00) vs substring F1=0.71 (P=0.56, 'may in' trả 16/20 vì 'in' nằm trong 'Inverter') vs token tiền tố F1=0.77 — nên khớp token nguyên vẹn, AND trên mọi token. (2) unicodedata NFD ngây thơ KHÔNG xử lý 'đ' (không phải category Mn) — hỏng 8/20 sản phẩm, 'Ngăn đá' fold ra 'ngan đa' nên gõ 'ngan da' ra 0 kết quả mà không ném lỗi; bắt buộc map đ/Đ->d riêng SAU bước NFD. (3) SQLite FTS5 có sẵn (3.51.2) nhưng remove_diacritics dựng sẵn vẫn hỏng ở 'đ', nên vẫn phải fold ở tầng ứng dụng — mất đúng lợi ích chính, không đáng thêm tầng lưu trữ cho 28KB dữ liệu tĩnh. Trade-off chấp nhận: precision 0.83, 4 FP đều do 'tủ'/'từ' cùng fold thành 'tu' — va chạm đồng tự cố hữu của bỏ dấu, recall vẫn 1.00.

---
id: DEC-2
status: active
date: 2026-07-19
actor: "user:duyluuhx07@gmail.com"
ts: "2026-07-19T08:58:29.289827+00:00"
affects: "pyproject.toml, phase 1 setup, Dockerfile, quy trinh cai dat"
---

## DEC-2 — Khong cai project nhu package: bo 'pip install -e .', cai thang dependency da ghim

OBSERVED, tai lap that trong dir mo phong layout repo: pyproject.toml khong co [build-system]/[tool.setuptools] + 'uv pip install -e .' -> setuptools tu choi build voi 'Multiple top-level packages discovered in a flat-layout: [app, data, plans, harness]'. Repo nay la ung dung chay tai cho, KHONG phai package phan phoi: da co pythonpath=[.] trong [tool.pytest.ini_options] de 'import app.*' chay, nen '-e .' mau thuan voi chinh cau hinh do chu khong bo sung. Chot: cai thang 5 dep ghim (fastapi==0.139.2, uvicorn==0.51.0, pydantic==2.13.4, pytest==9.1.1, httpx2==2.7.0); Dockerfile dua app/ vao image bang COPY, khong bang cai package. He qua: them [build-system] ve sau se lam song lai dung loi da giet phase 1.

---
id: DEC-3
status: active
date: 2026-07-19
actor: "user:duyluuhx07@gmail.com"
ts: "2026-07-19T08:58:45.033576+00:00"
affects: "GET /products, GET /products/search, loc category, loc brand, app/loader.py resolve_category, app/search.py"
---

## DEC-3 — Filter category nhan CA slug LAN ten hien thi da fold; chuoi rong KHONG phai 'khong loc'

Dataset co ca hai truong: product.category la slug ('refrigerator') va product.category_name la ten hien thi ('Tu Lanh'); d['categories'] cho san map slug->name. Nguoi dung chot nhan ca hai, nen dung 'category_map' fold(name)|slug -> slug dung luc khoi dong; OBSERVED ca bon cach viet (refrigerator / tu lanh / Tu Lanh / TU LANH) deu ra 4 ket qua. Kem theo: guard PHAI la 'is not None', TUYET DOI khong dung truthiness. OBSERVED tren FastAPI that: '?category=' bind thanh chuoi rong '' chu khong phai None, nen 'if category:' bo qua bo loc va tra ve CA 20 san pham trong khi 'if category is not None:' resolve '' -> None -> 0 ket qua. Hai cai deu tra 200 va deu pass moi test co ten trong plan, nhung lech nhau 20 vs 0 - day la che do hong-tra-200 thu tu, cung ho voi thu tu route va lifespan.
