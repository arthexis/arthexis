"""Integration tests for the shop storefront flow."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.odoo.models import OdooProduct
from apps.shop.models import Shop, ShopOrder, ShopProduct


@pytest.mark.django_db
def test_checkout_creates_order_items_and_odoo_reference(client):
    """Posting checkout data should persist order lines and sync to Odoo reference."""

    shop = Shop.objects.create(name="RFID Store", slug="rfid-store")
    odoo_product = OdooProduct.objects.create(
        name="RFID Bundle",
        renewal_period=30,
        description="RFID card pack",
        odoo_product={"id": 42, "name": "RFID Bundle"},
    )
    product = ShopProduct.objects.create(
        shop=shop,
        name="RFID Cards (10x)",
        slug="rfid-cards-10x",
        unit_price=Decimal("19.90"),
        currency="EUR",
        stock_quantity=20,
        odoo_product=odoo_product,
    )

    add_url = reverse("shop-add-to-cart", kwargs={"slug": shop.slug, "product_id": product.id})
    checkout_url = reverse("shop-checkout", kwargs={"slug": shop.slug})

    add_response = client.post(add_url, {"quantity": 2})
    assert add_response.status_code == 302

    checkout_response = client.post(
        checkout_url,
        {
            "customer_name": "Ada Lovelace",
            "customer_email": "ada@example.com",
            "payment_provider": "stripe",
            "shipping_address_line1": "1 Example Street",
            "shipping_address_line2": "Suite 5",
            "shipping_city": "Paris",
            "shipping_postal_code": "75000",
            "shipping_country": "France",
        },
    )
    assert checkout_response.status_code == 302

    order = ShopOrder.objects.get(shop=shop)
    assert order.total == Decimal("39.80")
    assert order.items.count() == 1
    assert order.items.first().line_total == Decimal("39.80")
    assert order.odoo_sales_order_ref.startswith("SO-RFID-STORE-")


@pytest.mark.django_db
def test_tracking_page_returns_order_when_email_matches(client):
    """Tracking lookup should render order status for the right email + order id."""

    shop = Shop.objects.create(name="Readers", slug="readers")
    order = ShopOrder.objects.create(
        shop=shop,
        customer_name="Grace Hopper",
        customer_email="grace@example.com",
        payment_provider="manual",
        shipping_address_line1="42 Harbor Ave",
        shipping_city="New York",
        shipping_postal_code="10001",
        shipping_country="USA",
        subtotal=Decimal("99.00"),
        shipping_cost=Decimal("0.00"),
        total=Decimal("99.00"),
        currency="EUR",
        tracking_number="TRK123",
    )

    url = reverse("shop-order-track", kwargs={"slug": shop.slug})
    response = client.get(url, {"order_id": order.id, "customer_email": order.customer_email})

    assert response.status_code == 200
    assert f"Order #{order.id}" in response.content.decode()
    assert "TRK123" in response.content.decode()


@pytest.mark.django_db
def test_tracking_page_404s_for_wrong_email(client):
    """Tracking lookup should reject mismatched email addresses."""

    shop = Shop.objects.create(name="Readers", slug="readers")
    order = ShopOrder.objects.create(
        shop=shop,
        customer_name="Grace Hopper",
        customer_email="grace@example.com",
        payment_provider="manual",
        shipping_address_line1="42 Harbor Ave",
        shipping_city="New York",
        shipping_postal_code="10001",
        shipping_country="USA",
        subtotal=Decimal("99.00"),
        shipping_cost=Decimal("0.00"),
        total=Decimal("99.00"),
        currency="EUR",
    )

    url = reverse("shop-order-track", kwargs={"slug": shop.slug})
    response = client.get(url, {"order_id": order.id, "customer_email": "wrong@example.com"})

    assert response.status_code == 404
