from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from .models import ShopProduct


class CartValidationError(ValueError):
    """Raised when cart entries reference invalid or unavailable products."""


def calculate_cart_total(entries: Iterable[dict]) -> Decimal:
    """Return the total amount for serialized cart entries."""

    total = Decimal("0.00")
    for entry in entries:
        total += Decimal(entry["unit_price"]) * int(entry["quantity"])
    return total


def serialize_product_for_cart(product: ShopProduct, quantity: int) -> dict:
    """Convert a product and quantity to the session cart schema."""

    if quantity < 1:
        raise CartValidationError("Quantity must be positive.")
    if quantity > product.stock_quantity:
        raise CartValidationError("Requested quantity is not in stock.")

    return {
        "product_id": product.id,
        "shop_id": product.shop_id,
        "name": product.name,
        "sku": product.sku,
        "unit_price": str(product.unit_price),
        "quantity": quantity,
        "odoo_product_id": product.odoo_product_id,
    }
