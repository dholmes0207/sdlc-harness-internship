"""FastAPI app: `lifespan` nap dataset + dung token index luc khoi dong, 4
route theo DUNG thu tu (docs/system-architecture.md:25, quyet dinh #9). Day
la module DUY NHAT trong `app/` duoc import `fastapi`/`starlette`
(docs/code-standards.md:14-15) - tang duoi (`search`, `index`, `text`,
`loader`) khong duoc biet gi ve HTTP.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from app.index import build_token_index
from app.loader import build_category_map, load_dataset
from app.models import HealthOut, PaginationEnvelope, ProductOut
from app.search import run as run_search


class ListParams(BaseModel):
    """Tham so chung cho GET /products - loc + phan trang, khong co `q`."""

    category: str | None = None
    brand: str | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def _check_price_range(self) -> "ListParams":
        """min_price > max_price -> 422 native qua ValueError (quyet dinh
        #12). Khong tu che HTTPException(422) - se tao hai dinh dang loi
        song song, vi pham docs/code-standards.md:72."""
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price khong duoc lon hon max_price")
        return self


class SearchParams(ListParams):
    """Ke thua ListParams, them `q`.

    `q` PHAI nam BEN TRONG model nay, KHONG duoc tach thanh tham so roi cua
    handler. OBSERVED (RK10): tron `q: Annotated[str, Query(...)]` voi mot
    query model rieng khien FastAPI coi model do la field bat buoc ten "f" ->
    422 cho MOI request, ke ca request hop le. Dung ke thua giai quyet triet
    de bay nay.
    """

    q: str = Field(min_length=1)

    @field_validator("q")
    @classmethod
    def _check_q_not_blank(cls, value: str) -> str:
        """`Query(min_length=1)` KHONG chan chuoi toan khoang trang (RK4).
        OBSERVED: `?q=%20%20` qua `min_length=1` tra 200. Validator nay them
        buoc `strip()` de chan dung."""
        if not value.strip():
            raise ValueError("q khong duoc chi la khoang trang")
        return value


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Nap dataset + dung token index dung MOT lan luc khoi dong, cat vao
    `app.state`. KHONG try/except nuot loi - nap hong PHAI lam app chet
    (RK13, docs/code-standards.md:78-79): mot API chay duoc nhung tra 0 ket
    qua cho moi truy van la che do hong te hon crash."""
    dataset = load_dataset()
    token_index = build_token_index(dataset.products)
    category_map = build_category_map(dataset.categories)
    app.state.dataset = dataset
    app.state.token_index = token_index
    app.state.category_map = category_map
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health", response_model=HealthOut)
def get_health() -> HealthOut:
    """`products_loaded` PHAI la `len(dataset.products)` = 20, TUYET DOI
    khong phai `len(token_index)` = 90 (so token phan biet) - xem
    Requirements #8 phase-5-api-routes.md (VL-13). Endpoint nay con la canary
    cho RK3 (fixture thieu `with`) va RK13 (nap hong): ca hai keo gia tri nay
    ve 0."""
    dataset = app.state.dataset
    return HealthOut(status="ok", products_loaded=len(dataset.products))


@app.get("/products", response_model=PaginationEnvelope)
def list_products(params: Annotated[ListParams, Query()]) -> PaginationEnvelope:
    """Chi dieu phoi: parse params -> goi search.run() -> boc phong bi. Khong
    logic loc/sort/phan trang o day - thuoc phase 4. Truyen thang `category`
    tho xuong `search.run()`, noi do goi `resolve_category()`."""
    total, items = run_search(
        app.state.dataset,
        app.state.token_index,
        app.state.category_map,
        category=params.category,
        brand=params.brand,
        min_price=params.min_price,
        max_price=params.max_price,
        page=params.page,
        page_size=params.page_size,
    )
    return PaginationEnvelope(
        total=total, page=params.page, page_size=params.page_size, items=items
    )


# CANH BAO (RK2, docs/system-architecture.md:77-79): route nay PHAI khai bao
# TRUOC "/products/{product_id}" ben duoi. Dao thu tu KHONG gay 404 - FastAPI
# se khop "search" nhu mot gia tri {product_id} va tra 200 voi than
# {"hit":"by_id","product_id":"search"}. OBSERVED that tren FastAPI 0.139.2 /
# starlette 1.3.1 trong phien lap ke hoach nay.
@app.get("/products/search", response_model=PaginationEnvelope)
def search_products(params: Annotated[SearchParams, Query()]) -> PaginationEnvelope:
    """Chi dieu phoi, giong list_products nhung co `q`."""
    total, items = run_search(
        app.state.dataset,
        app.state.token_index,
        app.state.category_map,
        q=params.q,
        category=params.category,
        brand=params.brand,
        min_price=params.min_price,
        max_price=params.max_price,
        page=params.page,
        page_size=params.page_size,
    )
    return PaginationEnvelope(
        total=total, page=params.page, page_size=params.page_size, items=items
    )


# CANH BAO (RK2): route nay PHAI khai bao SAU "/products/search" o tren. Neu
# doi cho, moi request toi /products/search se bi khop vao day truoc voi
# product_id="search", tra 200 sai thay vi ket qua tim kiem that.
@app.get("/products/{product_id}", response_model=ProductOut)
def get_product_by_id(product_id: str) -> ProductOut:
    """Lay-theo-id KHONG bi luat loc gia dung toi - san pham gia null van
    hien o day du bi an khoi list/search co loc gia."""
    for product in app.state.dataset.products:
        if product["id"] == product_id:
            return ProductOut(**product)
    raise HTTPException(
        status_code=404, detail=f"Khong tim thay san pham: {product_id}"
    )
