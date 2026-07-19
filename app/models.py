"""Pydantic v2 response models: san pham, phong bi phan trang, health check.

Chi import pydantic - khong duoc import web framework (docs/code-standards.md,
chi tang route moi duoc biet ve HTTP).
"""

from pydantic import BaseModel


class ProductOut(BaseModel):
    """Mot san pham trong response.

    Loai khoi response: availability (OBSERVED "unknown" 20/20 - vo nghia),
    image_url (OBSERVED null 20/20), specifications (43 key roi rac, ngoai
    scope), product_id_web, model_code.
    """

    id: str
    sku: str
    name: str
    slug: str
    category: str
    category_name: str
    brand: str
    original_price: float | None
    sale_price: float | None
    effective_price: float | None
    discount_percent: float | None
    currency: str
    promotion: str | None


class PaginationEnvelope(BaseModel):
    """Phong bi phan trang - thuat ngu da dang ky trong docs/glossary.yaml.

    `total` = so ban ghi khop sau loc, TRUOC khi cat trang - khac
    data["total"] cua dataset (= 20, tong so ban ghi trong file).
    """

    total: int
    page: int
    page_size: int
    items: list[ProductOut]


class HealthOut(BaseModel):
    """Response cho health check."""

    status: str
    products_loaded: int
