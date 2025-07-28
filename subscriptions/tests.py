from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from .models import Product, Subscription


class SubscriptionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="bob", password="pwd")
        self.product = Product.objects.create(name="Gold", renewal_period=30)

    def test_create_and_list_subscription(self):
        response = self.client.post(
            reverse("add-subscription"),
            data={"user_id": self.user.id, "product_id": self.product.id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Subscription.objects.count(), 1)

        list_resp = self.client.get(
            reverse("subscription-list"), {"user_id": self.user.id}
        )
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertEqual(len(data["subscriptions"]), 1)
        self.assertEqual(data["subscriptions"][0]["product__name"], "Gold")

    def test_product_list(self):
        response = self.client.get(reverse("product-list"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["products"]), 1)
        self.assertEqual(data["products"][0]["name"], "Gold")

