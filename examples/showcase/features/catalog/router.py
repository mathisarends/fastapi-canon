from fastapi_canon import APIRouter, Inject

from .errors import product_not_found, product_unavailable
from .models import Product, Reservation
from .service import CatalogService

router = APIRouter(
    prefix="/products",
    tags=["catalog"],
)


@router.get("/{product_id}", operation_id="get_product", raises=[product_not_found])
async def get_product(
    product_id: str,
    catalog: Inject[CatalogService],
) -> Product:
    return catalog.get_product(product_id)


@router.post(
    "/{product_id}/reservations",
    operation_id="reserve_product",
    raises=[product_not_found, product_unavailable],
)
async def reserve_product(
    product_id: str,
    reservation: Reservation,
    catalog: Inject[CatalogService],
) -> Product:
    return catalog.reserve_product(product_id, reservation.quantity)
