"""Test cho app/models.py: Pydantic v2 response models.

ProductOut, PaginationEnvelope, HealthOut. Xem
plans/260719-1435-product-search-api/phases/phase-3-loader-models.md.
"""

from app.loader import load_dataset


def test_pagination_envelope_has_exactly_4_fields() -> None:
    """Invariant khoa hinh dang hop dong: dung 4 truong, khong hon khong kem."""
    from app.models import PaginationEnvelope

    assert set(PaginationEnvelope.model_fields) == {
        "total",
        "page",
        "page_size",
        "items",
    }


def test_product_out_excludes_dead_fields() -> None:
    """availability, image_url, specifications khong duoc nam trong
    model_fields - cac truong nay vo nghia hoac ngoai scope (OBSERVED)."""
    from app.models import ProductOut

    dead_fields = {
        "availability",
        "image_url",
        "specifications",
        "product_id_web",
        "model_code",
    }
    assert dead_fields.isdisjoint(ProductOut.model_fields)


def test_product_out_validates_real_product() -> None:
    """ProductOut.model_validate chay duoc tren ca 20 san pham that, khong
    nem loi - ke ca printer-318469 voi gia null."""
    from app.models import ProductOut

    dataset = load_dataset()
    for product in dataset.products:
        ProductOut.model_validate(product)

    assert len(dataset.products) == 20
