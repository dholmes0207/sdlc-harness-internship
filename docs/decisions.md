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
