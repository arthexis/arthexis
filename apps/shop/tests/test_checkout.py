from datetime import datetime, time
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cards.models import OfferingSoul
from apps.shop.models import Shop, ShopOrder, ShopProduct
from apps.souls.models import ShopOrderSoulAttachment, Soul
from apps.survey.models import Survey, SurveyResponse


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

    def test_order_number_keeps_prefix_and_length_limit(self):
        """Generated order number should preserve SO prefix within max length."""

        shop = Shop.objects.create(name="Prefix Shop", slug="prefix-shop")
        order = ShopOrder.objects.create(
            shop=shop,
            customer_name="User",
            customer_email="user@example.com",
            shipping_address_line1="Address",
            shipping_city="Porto",
            shipping_postal_code="1234",
            shipping_country="Portugal",
        )

        self.assertTrue(order.order_number.startswith("SO"))
        self.assertLessEqual(len(order.order_number), 20)

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

    def test_add_to_cart_clears_items_from_different_shop(self):
        """Adding an item from another shop should clear existing cart items."""

        shop_a = Shop.objects.create(name="Shop A", slug="shop-a")
        shop_b = Shop.objects.create(name="Shop B", slug="shop-b")
        product_a = ShopProduct.objects.create(
            shop=shop_a,
            name="Product A",
            sku="A-1",
            unit_price=Decimal("10.00"),
            stock_quantity=10,
        )
        product_b = ShopProduct.objects.create(
            shop=shop_b,
            name="Product B",
            sku="B-1",
            unit_price=Decimal("15.00"),
            stock_quantity=10,
        )

        self.client.post(
            reverse("shop:add_to_cart", kwargs={"shop_slug": shop_a.slug, "product_id": product_a.id}),
            {"quantity": 1},
            follow=True,
        )
        self.client.post(
            reverse("shop:add_to_cart", kwargs={"shop_slug": shop_b.slug, "product_id": product_b.id}),
            {"quantity": 1},
            follow=True,
        )

        session_cart = self.client.session.get("shop_cart", {})
        self.assertEqual(set(session_cart.keys()), {str(product_b.id)})

    def test_checkout_rejects_stale_cart_entries(self):
        """Checkout should reject carts containing deleted or inactive products."""

        shop = Shop.objects.create(name="Stale Cart Shop", slug="stale-cart")
        product = ShopProduct.objects.create(
            shop=shop,
            name="Temporary Product",
            sku="TMP-1",
            unit_price=Decimal("5.00"),
            stock_quantity=2,
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

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Some cart items are no longer available")
        self.assertEqual(ShopOrder.objects.count(), 0)


class ShopIndexTests(TestCase):
    """Coverage for shop listing behavior and closed-state messaging."""

    def test_index_shows_generic_closed_message_when_no_shops_exist(self):
        """The index should show a generic closed message when there are no active shops."""

        response = self.client.get(reverse("shop:index"))

        self.assertContains(response, "Our shop is closed at the moment")
        self.assertNotContains(response, "No active shops are available")

    def test_index_shows_next_opening_time_when_all_shops_closed_by_hours(self):
        """When all shops are closed by schedule, the next opening time should be shown."""

        Shop.objects.create(
            name="Morning Shop",
            slug="morning-shop",
            opening_time=time(9, 0),
            closing_time=time(17, 0),
        )
        current_timezone = timezone.get_current_timezone()
        now = timezone.make_aware(datetime(2026, 1, 1, 7, 30), current_timezone)

        with patch("apps.shop.views.timezone.localtime", return_value=now):
            response = self.client.get(reverse("shop:index"))

        self.assertContains(response, "Our shop is closed at the moment")
        self.assertContains(response, "It will next open at 09:00")

    def test_index_hides_closed_message_when_a_shop_is_currently_open(self):
        """When an in-hours shop exists, the shop should render instead of closed messaging."""

        Shop.objects.create(
            name="Open Shop",
            slug="open-shop",
            opening_time=time(9, 0),
            closing_time=time(17, 0),
        )
        current_timezone = timezone.get_current_timezone()
        now = timezone.make_aware(datetime(2026, 1, 1, 10, 0), current_timezone)

        with patch("apps.shop.views.timezone.localtime", return_value=now):
            response = self.client.get(reverse("shop:index"))

        self.assertContains(response, "Open Shop")
        self.assertNotContains(response, "Our shop is closed at the moment")


class ShopBusinessHoursValidationTests(TestCase):
    """Validation and constraint coverage for optional business hours."""

    def test_shop_clean_rejects_half_configured_business_hours(self):
        """Model validation should require opening and closing times as a pair."""

        shop = Shop(name="Invalid Hours Shop", slug="invalid-hours", opening_time=time(9, 0), closing_time=None)

        with self.assertRaises(ValidationError):
            shop.clean()

    def test_shop_constraint_rejects_half_configured_business_hours(self):
        """Database constraint should reject records with only one business-hours field set."""

        with self.assertRaises(IntegrityError):
            Shop.objects.create(name="Invalid Constraint Shop", slug="invalid-constraint", opening_time=time(9, 0))


class ShopHoursEnforcementTests(TestCase):
    """Server-side order flow checks for closed shops."""

    def test_add_to_cart_rejects_closed_shop(self):
        """Adding to cart should be blocked when the target shop is currently closed."""

        shop = Shop.objects.create(name="Night Shop", slug="night-shop", opening_time=time(22, 0), closing_time=time(5, 0))
        product = ShopProduct.objects.create(
            shop=shop,
            name="Moonlight Item",
            sku="N-1",
            unit_price=Decimal("10.00"),
            stock_quantity=4,
        )
        current_timezone = timezone.get_current_timezone()
        now = timezone.make_aware(datetime(2026, 1, 1, 12, 0), current_timezone)

        with patch("apps.shop.views.timezone.localtime", return_value=now):
            response = self.client.post(
                reverse("shop:add_to_cart", kwargs={"shop_slug": shop.slug, "product_id": product.id}),
                {"quantity": 1},
                follow=True,
            )

        self.assertContains(response, "currently closed and cannot accept orders")
        self.assertEqual(self.client.session.get("shop_cart", {}), {})

    def test_checkout_rejects_when_shop_is_closed(self):
        """Checkout should fail for a stale cart when the shop has since closed."""

        shop = Shop.objects.create(name="Day Shop", slug="day-shop", opening_time=time(9, 0), closing_time=time(17, 0))
        product = ShopProduct.objects.create(
            shop=shop,
            name="Desk Reader",
            sku="D-1",
            unit_price=Decimal("20.00"),
            stock_quantity=6,
        )

        open_time = timezone.make_aware(datetime(2026, 1, 1, 10, 0), timezone.get_current_timezone())
        with patch("apps.shop.views.timezone.localtime", return_value=open_time):
            self.client.post(
                reverse("shop:add_to_cart", kwargs={"shop_slug": shop.slug, "product_id": product.id}),
                {"quantity": 1},
                follow=True,
            )

        closed_time = timezone.make_aware(datetime(2026, 1, 1, 20, 0), timezone.get_current_timezone())
        with patch("apps.shop.views.timezone.localtime", return_value=closed_time):
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

        self.assertContains(response, "currently closed and cannot accept orders")
        self.assertEqual(ShopOrder.objects.count(), 0)


class ShopNextOpeningDatetimeTests(TestCase):
    """Edge cases for next-opening calculations."""

    def test_next_opening_datetime_for_overnight_shop_after_midnight(self):
        """Overnight shops should reopen on the next day once the current window is active."""

        shop = Shop(name="Overnight", slug="overnight", opening_time=time(22, 0), closing_time=time(5, 0))
        reference = timezone.make_aware(datetime(2026, 1, 2, 3, 0), timezone.get_current_timezone())

        next_opening = shop.next_opening_datetime(reference)

        self.assertEqual(next_opening, timezone.make_aware(datetime(2026, 1, 2, 22, 0), timezone.get_current_timezone()))

    def test_next_opening_datetime_for_overnight_shop_at_opening_boundary(self):
        """Overnight shops should return the next day at the exact opening-time boundary."""

        shop = Shop(name="Overnight", slug="overnight-boundary", opening_time=time(22, 0), closing_time=time(5, 0))
        current_timezone = timezone.get_current_timezone()
        reference = timezone.make_aware(datetime(2026, 1, 2, 22, 0), current_timezone)

        next_opening = shop.next_opening_datetime(reference)

        self.assertEqual(next_opening, timezone.make_aware(datetime(2026, 1, 3, 22, 0), current_timezone))


class ShopSoulSeedPreloadTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(
            name="Seed Shop",
            slug="seed-shop",
            default_payment_provider="stripe",
        )
        self.card_product = ShopProduct.objects.create(
            shop=self.shop,
            name="Soul Card",
            sku="SOUL-CARD-1",
            unit_price=Decimal("29.90"),
            stock_quantity=20,
            supports_soul_seed_preload=True,
        )
        self.poster_product = ShopProduct.objects.create(
            shop=self.shop,
            name="Poster",
            sku="POSTER-1",
            unit_price=Decimal("9.90"),
            stock_quantity=20,
            supports_soul_seed_preload=False,
        )

    def _create_soul_for_email(self, email: str) -> Soul:
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        username_base = email.split("@", 1)[0]
        username = f"{username_base}-{user_model.objects.count() + 1}"
        user = user_model.objects.create_user(
            username=username,
            email=email,
            password="x",
        )
        core_hash = f"{user.id:064x}"
        offering = OfferingSoul.objects.create(
            core_hash=core_hash,
            package={
                "schema_version": "1.0",
                "core_hash": core_hash,
                "issuance_marker": "checkout",
                "metadata": {"size_bytes": 2},
                "traits": {"structural": {}, "type_aware": {}},
            },
            structural_traits={},
            type_traits={},
        )
        survey, _ = Survey.objects.get_or_create(
            title="Soul Seed Registration",
            defaults={"is_active": True},
        )
        response = SurveyResponse.objects.create(survey=survey, participant_token=f"pt-{user.id}")
        return Soul.objects.create(
            user=user,
            offering_soul=offering,
            survey_response=response,
            soul_id=f"soul-{user.id}",
            survey_digest=f"digest-{user.id}",
            package={"schema_version": "1.0"},
            email_hash=f"hash-{user.id}",
        )

    def _add_to_cart(self, product: ShopProduct, quantity: int = 1) -> None:
        self.client.post(
            reverse("shop:add_to_cart", kwargs={"shop_slug": self.shop.slug, "product_id": product.id}),
            {"quantity": quantity},
            follow=True,
        )

    def _checkout(self, email: str):
        return self.client.post(
            reverse("shop:checkout"),
            {
                "customer_name": "Jane Buyer",
                "customer_email": email,
                "shipping_address_line1": "42 Main Street",
                "shipping_address_line2": "",
                "shipping_city": "Madrid",
                "shipping_postal_code": "28001",
                "shipping_country": "Spain",
            },
            follow=True,
        )

    def test_checkout_preloads_soul_seed_for_card_products(self):
        soul = self._create_soul_for_email("seed@example.com")
        self._add_to_cart(self.card_product, quantity=2)
        self._add_to_cart(self.poster_product, quantity=1)

        response = self._checkout("seed@example.com")

        self.assertEqual(response.status_code, 200)
        attachments = list(ShopOrderSoulAttachment.objects.select_related("order_item").order_by("order_item__id"))
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].soul_id, soul.id)
        self.assertEqual(attachments[0].order_item.product_id, self.card_product.id)

    def test_checkout_skips_preload_when_no_unique_soul_for_email(self):
        self._create_soul_for_email("dupe@example.com")
        self._create_soul_for_email("dupe@example.com")
        self._add_to_cart(self.card_product, quantity=1)

        response = self._checkout("dupe@example.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ShopOrderSoulAttachment.objects.count(), 0)
