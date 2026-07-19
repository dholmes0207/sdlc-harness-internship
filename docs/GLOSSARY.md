---
id: harness.glossary
type: glossary
status: stable
version: 3.1.0
owner: maintainer
---

# Glossary — SDLC Harness shared language

<!-- generated from docs/glossary.yaml by glossary_register.py — edit the YAML SSOT (or run glossary_register.py --add), not this file -->

Canonical vocabulary for the harness. Agents **read this before naming** variables, files, hooks, or prose, so one settled term replaces a paragraph of re-explanation and naming stays consistent across the tree. English by convention — the instruction surface (skills, rules, agents, CLAUDE.md) is English even when generated reports are not (see `harness/data/output.yaml`).

**This file is a generated VIEW of `docs/glossary.yaml`.** Do NOT edit it by hand — a render overwrites it. To coin a new load-bearing term, run `glossary_register.py --add` (human-approved) or edit the YAML SSOT, then re-render. `hs:plan` and `hs:discover` read the glossary before naming. The `Forbidden wording` column is the human-readable side of the bans that `harness/tests/test_bug_class_invariants.py` enforces over `harness/`.

| Term | Definition | Forbidden wording | Backing |
|---|---|---|---|
| accent fold | Chuan hoa chuoi tieng Viet ve ASCII thuong: NFD + bo ky tu Mn, ROI map d/D->d rieng. Buoc map d la bat buoc - NFD mot minh sot d o 8/20 san pham. | normalize / slugify | docs/decisions.md#DEC-1 |
| effective price | Gia dung de loc: effective_price, fallback original_price. Null ca hai -> san pham VAN hien khi list/search nhung BI LOAI khi co min_price/max_price. Dung 1 san pham: printer-318469. | final price / real price | docs/system-architecture.md |
| pagination envelope | Response boc ngoai gom total, page, page_size, items. total = so ban ghi khop sau loc TRUOC khi cat trang - KHAC key total=20 co san trong data/products.json. | wrapper / result set | docs/system-architecture.md |
| token index | dict[token -> set(product_id)] dung luc khoi dong tu name + brand + category_name da fold. Khop token NGUYEN VEN, AND tren moi token cua truy van. | search index / inverted index | docs/decisions.md#DEC-1 |
