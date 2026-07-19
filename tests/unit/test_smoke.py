"""Smoke test: xac nhan moi truong build/dev da dung truoc khi viet logic that.

Ba test dau la RED test (Phase 1 TDD) - phai do vi thieu dependency, khong phai
vi thieu interpreter. Test cuoi la Tests-After (khong phai RED) - no chi doc
mot file da co san trong repo nen pass voi 0 dong implementation cua phase 1.
"""

import json
from pathlib import Path


def test_fastapi_importable() -> None:
    """Khoa: dependency fastapi da duoc cai that trong moi truong chay."""
    import fastapi  # noqa: F401


def test_testclient_usable() -> None:
    """Khoa: httpx2 dung goi (khong phai httpx).

    Chi import TestClient khong du de bat loi - starlette/testclient.py
    import httpx2 mot cach lazy, loi chi bung khi khoi tao TestClient.
    """
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    client = TestClient(FastAPI())
    assert client is not None


def test_app_package_importable() -> None:
    """Khoa that su: `pythonpath = ["."]` trong pyproject.toml can thiet de
    console script pytest (`.venv/bin/pytest`) import duoc `app` khi chay tu
    ben ngoai cay goi `tests/`.

    Vi sao khong the chi `import app` truc tiep tai day (da phat hien la
    guard gia - RK20, giong loi da xay ra o A10 va A12): ca hai duong chay -
    `python -m pytest` (tu chen cwd vao sys.path[0], hanh vi chuan cua co
    `-m`) LAN console script `.venv/bin/pytest` goi tu repo root (tu chen repo
    root vao sys.path qua co che "prepend import mode" cua chinh pytest, vi
    `tests/__init__.py` va `tests/unit/__init__.py` tao thanh mot chuoi goi len
    toi repo root) - DEU lam `import app` thanh cong BAT KE `pythonpath` co
    duoc cau hinh hay khong. Da kiem chung thuc nghiem ca ba truong hop nay
    deu PASS du bat/tat `pythonpath`, nen mot `import app` truc tiep khong
    khoa duoc gi.

    De thuc su khoa `pythonpath`, test nay chay mot file probe doc lap dat
    BEN NGOAI cay goi `tests/` (khong co chuoi `__init__.py` nao bao quanh no)
    tu MOT CWD KHAC repo root, thong qua console script `.venv/bin/pytest`
    (khong phai `python -m pytest`). Trong dieu kien do, khong co __init__.py
    chain va khong co cwd-insertion cua `-m`, nen nguon DUY NHAT khien
    `import app` thanh cong la `pythonpath = ["."]` trong pyproject.toml (pytest
    resolve "." tuong doi voi rootdir, khong phai cwd tien trinh). Da kiem
    chung: PASS khi `pythonpath` co mat, FAIL voi `ModuleNotFoundError: No
    module named 'app'` khi go dong do.
    """
    import subprocess
    import tempfile

    repo_root = Path(__file__).resolve().parent.parent.parent
    pytest_console_script = repo_root / ".venv" / "bin" / "pytest"
    pyproject_path = repo_root / "pyproject.toml"

    assert pytest_console_script.exists(), (
        f"Khong tim thay console script pytest tai {pytest_console_script}"
    )

    probe_source = "def test_probe_import_app():\n    import app  # noqa: F401\n"

    with tempfile.TemporaryDirectory() as probe_dir_name:
        probe_dir = Path(probe_dir_name)
        probe_file = probe_dir / "test_probe_pythonpath.py"
        probe_file.write_text(probe_source, encoding="utf-8")

        result = subprocess.run(
            [
                str(pytest_console_script),
                str(probe_file),
                "-q",
                "-c",
                str(pyproject_path),
                f"--rootdir={repo_root}",
            ],
            cwd=probe_dir,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 0, (
        "pythonpath = ['.'] khong hoat dong nhu ky vong - console script pytest "
        "khong import duoc 'app' tu ben ngoai cay goi tests/.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_dataset_is_envelope_with_20_products() -> None:
    """Khoa hinh dang du lieu tu dau (RK9): data/products.json la mot dict
    boc ngoai (khong phai list truc tiep), co dung 20 san pham va 5 danh muc.

    Day la test Tests-After, KHONG phai RED: file data/products.json da co
    san trong repo tu truoc phase 1, nen test nay pass ma khong can bat ky
    dong implementation nao cua phase 1.
    """
    dataset_path = Path(__file__).resolve().parent.parent.parent / "data" / "products.json"
    with dataset_path.open(encoding="utf-8") as dataset_file:
        dataset = json.load(dataset_file)

    assert isinstance(dataset, dict)
    assert len(dataset["products"]) == 20
    assert len(dataset["categories"]) == 5
