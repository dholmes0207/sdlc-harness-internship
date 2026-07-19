---
phase: 1
title: "Setup"
status: pending
plan: 260719-1435-product-search-api
created: 2026-07-19
harness_version: 5.3.0
harness_kit_digest: 251ed307796039124b44d71759b3f62d8bb9135c4bf3053156e38798587a50a8
harness_schema_version: 1.0
---

# Phase 1 — Setup

## Overview

Dựng nền: `pyproject.toml` với dependency **ghim bản cụ thể**, venv qua `uv`,
cấu hình pytest, khung thư mục `app/` + `tests/` theo đúng layout ở
`docs/code-standards.md:23-40`.

Đây **không** phải phase thủ tục. OBSERVED trong phiên này: repo **không có**
`pyproject.toml`, không `requirements.txt`, không `app/`, không `tests/`; và
`fastapi` / `uvicorn` / `starlette` / `httpx2` đều **MISSING** trên máy. Không
có phase này thì mọi phase sau không chạy được một dòng test nào.

**Phụ thuộc:** không. Đây là gốc của DAG.

## Files

**Create**

- `pyproject.toml`
- `app/__init__.py` (rỗng)
- `tests/__init__.py` (rỗng)
- `tests/unit/__init__.py` (rỗng)
- `tests/api/__init__.py` (rỗng)
- `tests/unit/test_smoke.py`

**Modify**

- `.gitignore` — thêm `.venv/`, `__pycache__/`, `.pytest_cache/`

Không file nào của phase này bị phase khác chạm.

## Requirements

**Functional**

1. `pyproject.toml` khai báo runtime deps: `fastapi`, `uvicorn`, `pydantic` —
   ghim `==`, không dùng `>=` (`docs/code-standards.md:90`).
2. Dev deps: `pytest`, **`httpx2`**. `httpx` **KHÔNG** thay thế được.
3. `requires-python = ">=3.13"` — **CHỐT** (plan.md VL-9). OBSERVED: host
   3.13.13, container `python:3.13-slim` 3.13.14 — cả hai thoả. Scope local-only,
   không có nhu cầu bản thấp hơn.
4. Cấu hình pytest trong `pyproject.toml`: `testpaths = ["tests"]`,
   `pythonpath = ["."]` để `import app.*` chạy được mà không cần cài package.
5. Venv tại `.venv/` dựng bằng `uv`.

6. **KHÔNG cài chính project.** Không `pip install -e .`, không
   `[build-system]`, không `[tool.setuptools]`. Cài **thẳng 5 dependency đã
   ghim** (quyết định **D-B** ở plan.md).

   **Vì sao — đã chạy thật, không phải lo xa.** Dựng đúng `pyproject.toml` mà
   bản kế hoạch trước mô tả, trong một thư mục mô phỏng layout repo này
   (`app/`, `data/`, `plans/`, `harness/` ở top level), rồi chạy
   `uv pip install -e ".[dev]"`:

   ```
   error: Multiple top-level packages discovered in a flat-layout:
          ['app', 'data', 'plans', 'harness'].
          setuptools will not proceed with this build.
   ```

   Build **bị từ chối**. Phase 1 chết tại chỗ và **không phase nào sau đó chạy
   được** — đây là lỗi nghiêm trọng nhất red-team tìm ra (RK15).

   Requirement #4 (`pythonpath = ["."]`) tồn tại **chính là** để `import app.*`
   chạy mà không cần cài package — `-e .` **mâu thuẫn** với nó, không bổ sung.
   Repo này là ứng dụng chạy tại chỗ, không phải package phân phối.

**Non-functional**

7. **Không** thêm dependency runtime nào ngoài ba cái trên nếu không ghi lý do
   (`docs/code-standards.md:90-92`).
8. `.venv/` không được commit.

### Bản ghim — OBSERVED qua `uv pip compile` chạy thật trong phiên này

```
fastapi==0.139.2
uvicorn==0.51.0
pydantic==2.13.4
pytest==9.1.1
httpx2==2.7.0
```

Transitive tự giải: `starlette==1.3.1`, `pydantic-core==2.46.4`,
`anyio==4.14.2`, `h11==0.16.0`, `typing-extensions==4.16.0`.

**`httpx2` là dev-dep BẮT BUỘC.** Anchor: `starlette/testclient.py:33` →
`import httpx2 as httpx`; thiếu nó thì `starlette/testclient.py:42` ném
`RuntimeError: The starlette.testclient module requires the httpx2 package`.
Máy đang có `httpx 0.28.1` — **sai gói** (OBSERVED).

## Implementation Steps

1. Viết `tests/unit/test_smoke.py` (xem TDD bên dưới) và tạo 4 file
   `__init__.py` rỗng: `app/`, `tests/`, `tests/unit/`, `tests/api/`.
2. Chạy bước RED bằng **`python3` hệ thống** — `.venv` chưa tồn tại ở thời điểm
   này:

   ```
   python3 -m pytest tests/unit/test_smoke.py -q
   ```

   Phải **FAIL** với `ModuleNotFoundError: No module named 'fastapi'`. OBSERVED:
   `fastapi` MISSING trên host, `pytest 9.1.1` thì có sẵn — nên lệnh này chạy
   được và đỏ **đúng lý do**.

   (Bản kế hoạch trước ghi gate là `.venv/bin/python` ngay từ bước RED. Sai:
   ở bước đó venv chưa có, test sẽ đỏ vì **thiếu interpreter**, không phải vì
   thiếu `fastapi` — đỏ sai lý do thì không phải RED thật. VL-20.)

3. Viết `pyproject.toml`: `[project]` với `name`, `version`, `requires-python`,
   `dependencies`; `[project.optional-dependencies] dev`;
   `[tool.pytest.ini_options]` với `testpaths` + `pythonpath`.
   **Không** `[build-system]`, **không** `[tool.setuptools]` (Requirements #6).
4. `uv venv .venv --python 3.13`
5. Cài **thẳng dependency đã ghim** — **KHÔNG** `-e .` (Requirements #6, RK15):

   ```
   uv pip install --python .venv/bin/python \
     fastapi==0.139.2 uvicorn==0.51.0 pydantic==2.13.4 \
     pytest==9.1.1 httpx2==2.7.0
   ```

6. Thêm `.venv/`, `__pycache__/`, `.pytest_cache/` vào `.gitignore`.
7. Chạy lại bằng `.venv/bin/python -m pytest tests/ -q` → xanh.

## TDD

### Tests-before (RED)

Chạy bằng **`python3` hệ thống**, không phải `.venv/bin/python` — venv chưa tồn
tại ở bước này (VL-20):

```
python3 -m pytest tests/unit/test_smoke.py -q
```

Cả ba test dưới đây **phải đỏ vì thiếu dependency**, không phải vì thiếu
interpreter:

- [ ] `test_fastapi_importable` — `import fastapi`. Khoá: dependency đã cài thật.
      → **FAIL** `ModuleNotFoundError: No module named 'fastapi'` (OBSERVED:
      `fastapi` MISSING trên host).
- [ ] `test_testclient_usable` — `from starlette.testclient import TestClient`
      **rồi khởi tạo** `TestClient(FastAPI())`. Khoá: **`httpx2` đúng gói**
      (RK5). Chỉ `import` thôi **không đủ** — `starlette/testclient.py:33`
      import lười, lỗi chỉ bung khi dùng.
- [ ] `test_app_package_importable` — `import app`. Khoá: `pythonpath` cấu hình
      đúng, `app/__init__.py` tồn tại. → **FAIL** trước khi có `pyproject.toml`.

### Implement

Các bước 3-6 ở `## Implementation Steps`.

### Tests After

- [ ] Ba test RED trên chuyển xanh.
- [ ] `test_dataset_is_envelope_with_20_products` — `json.load(...)` cho ra
      **`dict`** (không phải `list`), key `products` **đúng 20** phần tử, key
      `categories` **đúng 5**. Khoá RK9 ngay từ phase đầu.

      **Đây là test Tests-After, KHÔNG phải RED.** Nó chỉ đọc một file đã tồn
      tại sẵn trong repo, nên nó **pass với 0 dòng implementation** của phase 1 —
      xếp nó vào RED là tự lừa mình về chu trình đỏ→xanh (VL-20). Giá trị của nó
      là chốt hình dạng dữ liệu cho P3, và nó vẫn thuộc gate.
- [ ] `.venv/bin/python -c "import fastapi, starlette, httpx2, pydantic, pytest"`
      exit 0.

### Regression Gate

```
.venv/bin/python -m pytest tests/ -q
```

**MUST PASS 100%**, không skip, không `xfail` (`docs/code-standards.md:49`).
Commit `chore: scaffold project with pinned deps` khi gate xanh.

## Success

Tiêu chí đo được, không "cài xong là ổn":

- [ ] `.venv/bin/python -m pytest tests/ -q` → **4 passed, 0 failed, 0 skipped**.
- [ ] Bước RED (`python3 -m pytest`, trước khi có venv) cho **3 failed** với
      `ModuleNotFoundError`, **không** phải lỗi thiếu interpreter.
- [ ] `.venv/bin/python -c "import httpx2"` exit **0**.
- [ ] `TestClient(FastAPI())` khởi tạo được, **không ném** `RuntimeError`.
- [ ] `grep -cE "^\[build-system\]|^\[tool\.setuptools\]" pyproject.toml` →
      **0** (không cài project, RK15).
- [ ] `.venv/bin/python -c "import importlib.metadata as m; m.version('product-search-api')"`
      **thất bại** với `PackageNotFoundError` — chứng minh project **không** bị
      cài như package, chỉ có 5 dep.
- [ ] `grep -c "==" pyproject.toml` → **≥ 5** (5 dep ghim bản).
- [ ] `grep -c ">=" pyproject.toml` → **đúng 1** (chỉ `requires-python`) — không
      dep nào dùng range.
- [ ] `json.load(open("data/products.json"))["products"]` có **đúng 20** phần tử;
      `["categories"]` có **đúng 5**.
- [ ] `ls app/__init__.py tests/unit/__init__.py tests/api/__init__.py` exit 0.
- [ ] `git status --porcelain | grep -c "^?? \.venv"` → **0**.

## Risks

| Rủi ro | Khả năng | Tác động | Xử lý |
|---|---|---|---|
| **`pip install -e .` chết vì flat-layout (RK15)** | **C** — chắc chắn xảy ra nếu làm theo bản cũ | **C** — phase 1 chết, **không phase nào sau đó chạy** | **Đã chạy thật**: `Multiple top-level packages discovered in a flat-layout: ['app','data','plans','harness']`. Requirements #6 cấm `-e .`; success criteria kiểm `[build-system]` = 0 và `PackageNotFoundError` |
| Cài `httpx` thay `httpx2` (RK5) | **H** | **H** — chặn toàn bộ test API ở P5 | `test_testclient_usable` **khởi tạo** TestClient, không chỉ import. Bắt ở P1 thay vì P5 |
| Bước RED chạy bằng `.venv/bin/python` khi venv chưa có | **M** | **M** — test đỏ **sai lý do**, chu trình RED thành giả | Bước 2 ghi rõ dùng `python3` hệ thống; success criteria đòi lỗi là `ModuleNotFoundError` |
| `pythonpath` sai → `import app` chết ở mọi phase sau | M | M | `test_app_package_importable` bắt ngay |
| Dùng `>=` thay `==` | M | M — bản khác nhau giữa các lần chạy | Success criteria đếm `==` ≥5 và `>=` đúng 1 |
| `pydantic` venv (2.13.4) ≠ hệ thống (2.13.2) (RK14) | L | L | Luôn chạy qua `.venv/bin/python`, không `python3` trần |
| Commit nhầm `.venv/` | L | M | `.gitignore` + success criteria kiểm `git status` |
