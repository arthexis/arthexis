"""Service helpers for session-backed shop cart operations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist

from apps.shop.models import Shop, ShopProduct


class CartError(Exception):
    """Raised when cart operations fail due to invalid input."""


@dataclass
class CartLine:
    """Computed cart line representation for templates and checkout."""

    product: ShopProduct
    quantity: int
    line_total: Decimal


class ShopCart:
    """Session-backed cart keyed per shop."""

    SESSION_KEY = "shop_cart"

    def __init__(self, request, shop: Shop):
        """Initialize cart from session for the provided shop."""

        self.request = request
        self.shop = shop
        payload = request.session.get(self.SESSION_KEY, {})
        self._lines = payload.get(str(shop.pk), {})

    def _save(self) -> None:
        """Persist cart lines back to the user session."""

        payload = self.request.session.get(self.SESSION_KEY, {})
        payload[str(self.shop.pk)] = self._lines
        self.request.session[self.SESSION_KEY] = payload
        self.request.session.modified = True

    def add(self, product: ShopProduct, quantity: int = 1) -> None:
        """Add or increase product quantity."""

        if quantity < 1:
            raise CartError("Quantity must be at least 1.")
        key = str(product.pk)
        current = int(self._lines.get(key, 0))
        self._lines[key] = current + quantity
        self._save()

    def update(self, product: ShopProduct, quantity: int) -> None:
        """Set an exact quantity for a product, removing when zero."""

        if quantity < 0:
            raise CartError("Quantity cannot be negative.")
        key = str(product.pk)
        if quantity == 0:
            self._lines.pop(key, None)
        else:
            self._lines[key] = quantity
        self._save()

    def clear(self) -> None:
        """Remove all cart lines for this shop."""

        self._lines = {}
        self._save()

    def lines(self) -> list[CartLine]:
        """Return cart lines with product snapshots and computed totals."""

        items: list[CartLine] = []
        for product_id, quantity in self._lines.items():
            try:
                product = ShopProduct.objects.get(pk=product_id, shop=self.shop, is_active=True)
            except ObjectDoesNotExist:
                continue
            line_total = product.unit_price * quantity
            items.append(CartLine(product=product, quantity=quantity, line_total=line_total))
        return items

    def subtotal(self) -> Decimal:
        """Return current cart subtotal."""

        return sum((line.line_total for line in self.lines()), Decimal("0.00"))
