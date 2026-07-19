# Auto-Decision Ledger — 260719-1435-product-search-api

> Quyết-định con-AI TỰ ra ở chế-độ tự-quyết (KHÔNG phải sổ DEC user-duyệt `docs/decisions.md`). Sổ **chỉ để đọc, advisory** — không chặn việc gì. Nguồn sự-thật = `artifacts/auto-decisions.jsonl`; file này là VIEW sinh ra.

## ⚠ Phải soát (chưa)

| id | label | in_plan | skill/mode | what | why | evidence | reviewed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| e0bdfb60d89a | SCOPE | no | hs:cook/auto | Rewrote phase-1 test_app_package_importable from a bare 'import app' into a subprocess guard that spawns .venv/bin/pytest against a probe file in a tempdir outside the tests/ package chain, so it actually fails when pythonpath=['.'] is removed. | The phase file claimed this test locks 'pythonpath cau hinh dung' and predicted 3 RED failures. Both claims are false: CPython's -m inserts cwd at sys.path[0], AND pytest's prepend import mode inserts the repo root via the tests/__init__.py package chain. Proven: the original test passed with config nulled out (-c /dev/null). It was a third instance of the RK20 'guard gia' pattern the plan already fixed twice (A10, A12). User was asked and approved building a real guard rather than accepting or deleting it. | tests/unit/test_smoke.py:30-92 \| phase-1-setup.md:160-161 claims pythonpath guard \| 3-state toggle: present=1 passed, removed=1 failed ModuleNotFoundError: No module named 'app', restored=4 passed | no |


## Đã soát

_(none)_


## Chỉ truy-vết

_(none)_

