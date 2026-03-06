from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.shop.models import Shop, ShopOrder, ShopProduct


class ShopCheckoutTests(TestCase):
    """End-to-end tests for shop cart checkout and order tracking."""

    def test_checkout_creates_order_and_items(self):
        """Posting checkout data should create order and line items from cart."""

        shop = Shop.objects.create(
            name="RFID Store",
            slug="rfid-store",
            default_payment_provider="stripe",
        )
        product = ShopProduct.objects.create(
            shop=shop,
            name="RFID Card Bundle",
            sku="RFID-10",
            unit_price=Decimal("49.90"),
            stock_quantity=20,
        )

        add_url = reverse(
            "shop:add_to_cart",
            kwargs={"shop_slug": shop.slug, "product_id": product.id},
        )
        self.client.post(add_url, {"quantity": 2}, follow=True)

        checkout_url = reverse("shop:checkout")
        response = self.client.post(
            checkout_url,
            {
                "customer_name": "Jane Buyer",
                "customer_email": "jane@example.com",
                "shipping_address_line1": "42 Main Street",
                "shipping_address_line2": "",
                "shipping_city": "Madrid",
                "shipping_postal_code": "28001",
                "shipping_country": "Spain",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        order = ShopOrder.objects.get(customer_email="jane@example.com")
        self.assertEqual(order.total_amount, Decimal("99.80"))
        self.assertEqual(order.payment_provider, "stripe")
        self.assertEqual(order.items.count(), 1)

    def test_order_tracking_page(self):
        """Tracking page should be accessible with a valid tracking token."""

        shop = Shop.objects.create(name="Reader Shop", slug="reader-shop")
        order = ShopOrder.objects.create(
            shop=shop,
            customer_name="User",
            customer_email="user@example.com",
            shipping_address_line1="Address",
            shipping_city="Porto",
            shipping_postal_code="1234",
            shipping_country="Portugal",
        )

        url = reverse("shop:order_tracking", kwargs={"tracking_token": order.tracking_token})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.order_number)

    def test_checkout_rejects_mixed_shop_cart(self):
        """Checkout should fail when cart includes products from multiple shops."""

        first_shop = Shop.objects.create(name="Shop One", slug="shop-one")
        second_shop = Shop.objects.create(name="Shop Two", slug="shop-two")
        first_product = ShopProduct.objects.create(
            shop=first_shop,
            name="Product A",
            sku="A-1",
            unit_price=Decimal("10.00"),
            stock_quantity=10,
        )
        second_product = ShopProduct.objects.create(
            shop=second_shop,
            name="Product B",
            sku="B-1",
            unit_price=Decimal("20.00"),
            stock_quantity=10,
        )

        self.client.post(
            reverse("shop:add_to_cart", kwargs={"shop_slug": first_shop.slug, "product_id": first_product.id}),
            {"quantity": 1},
            follow=True,
        )
        self.client.post(
            reverse("shop:add_to_cart", kwargs={"shop_slug": second_shop.slug, "product_id": second_product.id}),
            {"quantity": 1},
            follow=True,
        )

        response = self.client.post(
            reverse("shop:checkout"),
            {
                "customer_name": "Jane Buyer",
                "customer_email": "jane@example.com",
                "shipping_address_line1": "42 Main Street",
                "shipping_address_line2": "",
                "shipping_city": "Madrid",
                "shipping_postal_code": "28001",
                "shipping_country": "Spain",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("shop:cart"))
        self.assertEqual(ShopOrder.objects.count(), 0)

    def test_checkout_rejects_stale_cart_product(self):
        """Checkout should gracefully fail when cart references a deleted product."""

        shop = Shop.objects.create(name="RFID Store", slug="rfid-store")
        product = ShopProduct.objects.create(
            shop=shop,
            name="RFID Card Bundle",
            sku="RFID-10",
            unit_price=Decimal("49.90"),
            stock_quantity=20,
        )

        self.client.post(
            reverse("shop:add_to_cart", kwargs={"shop_slug": shop.slug, "product_id": product.id}),
            {"quantity": 1},
            follow=True,
        )
        product.delete()

        response = self.client.post(
            reverse("shop:checkout"),
            {
                "customer_name": "Jane Buyer",
                "customer_email": "jane@example.com",
                "shipping_address_line1": "42 Main Street",
                "shipping_address_line2": "",
                "shipping_city": "Madrid",
                "shipping_postal_code": "28001",
                "shipping_country": "Spain",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("shop:cart"))
        self.assertEqual(ShopOrder.objects.count(), 0)
