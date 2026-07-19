"""Fixture dung chung cho toan bo test API.

`client` PHAI dung TestClient qua context manager (`with`) - day la bay da
xac minh that (RK3, xem
plans/260719-1435-product-search-api/phases/phase-5-api-routes.md). Neu khoi
tao TestClient(app) tran khong co `with`, lifespan KHONG chay -> token index
rong -> GET /products tra 200 {"total": 0, "items": []} ma khong nem loi nao.
Moi test API phai di qua fixture nay, cam khoi tao TestClient truc tiep trong
than test.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:  # 'with' la BAT BUOC - kich hoat lifespan
        yield test_client
