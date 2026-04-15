from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.cards.models import OfferingSoul
from apps.shop.models import Shop, ShopOrder, ShopProduct
from apps.souls.models import ShopOrderSoulAttachment, Soul
from apps.souls.services.checkout import CHECKOUT_SOUL_KEY
from apps.survey.models import Survey, SurveyResponse


class SoulCheckoutAttachmentTests(TestCase):
    def setUp(self):
        self.shop = Shop.objects.create(name="Soul Seed Shop", slug="soul-seed-shop")
        self.card_alpha = ShopProduct.objects.create(
            shop=self.shop,
            name="Soul Card Alpha",
            sku="SCA-1",
            unit_price=Decimal("15.00"),
            stock_quantity=20,
        )
        self.card_beta = ShopProduct.objects.create(
            shop=self.shop,
            name="Soul Card Beta",
            sku="SCB-1",
            unit_price=Decimal("20.00"),
            stock_quantity=20,
        )

        user = get_user_model().objects.create_user(
            username="seed-user",
            email="seed@example.com",
            password="x",
        )
        offering = OfferingSoul.objects.create(
            core_hash="f" * 64,
            package={
                "schema_version": "1.0",
                "core_hash": "f" * 64,
                "issuance_marker": "",
                "metadata": {"size_bytes": 2},
                "traits": {"structural": {}, "type_aware": {}},
            },
            structural_traits={},
            type_traits={},
        )
        survey = Survey.objects.create(title="Soul Seed Registration", is_active=True)
        survey_response = SurveyResponse.objects.create(survey=survey, participant_token="seed-cart")
        self.soul = Soul.objects.create(
            user=user,
            offering_soul=offering,
            survey_response=survey_response,
            soul_id="seed-soul-id",
            survey_digest="digest",
            package={"schema_version": "1.0"},
            email_hash="email-hash",
        )

    def test_checkout_sets_preload_quantity_from_item_quantity(self):
        self.client.post(
            reverse("shop:add_to_cart", kwargs={"shop_slug": self.shop.slug, "product_id": self.card_alpha.id}),
            {"quantity": 3},
            follow=True,
        )

        session = self.client.session
        session[CHECKOUT_SOUL_KEY] = self.soul.id
        session.save()

        self.client.post(
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

        order = ShopOrder.objects.get(customer_email="jane@example.com")
        attachment = ShopOrderSoulAttachment.objects.get(order_item__order=order)
        self.assertEqual(attachment.soul_id, self.soul.id)
        self.assertEqual(attachment.preload_quantity, 3)
        self.assertNotIn(CHECKOUT_SOUL_KEY, self.client.session)

    def test_checkout_tracks_each_card_line_item(self):
        self.client.post(
            reverse("shop:add_to_cart", kwargs={"shop_slug": self.shop.slug, "product_id": self.card_alpha.id}),
            {"quantity": 2},
            follow=True,
        )
        self.client.post(
            reverse("shop:add_to_cart", kwargs={"shop_slug": self.shop.slug, "product_id": self.card_beta.id}),
            {"quantity": 1},
            follow=True,
        )

        session = self.client.session
        session[CHECKOUT_SOUL_KEY] = self.soul.id
        session.save()

        self.client.post(
            reverse("shop:checkout"),
            {
                "customer_name": "Jane Buyer",
                "customer_email": "jane2@example.com",
                "shipping_address_line1": "42 Main Street",
                "shipping_address_line2": "",
                "shipping_city": "Madrid",
                "shipping_postal_code": "28001",
                "shipping_country": "Spain",
            },
            follow=True,
        )

        order = ShopOrder.objects.get(customer_email="jane2@example.com")
        attachments = ShopOrderSoulAttachment.objects.filter(order_item__order=order).order_by("order_item__sku")
        self.assertEqual(attachments.count(), 2)
        self.assertEqual([attachment.preload_quantity for attachment in attachments], [2, 1])
