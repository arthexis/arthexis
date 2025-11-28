from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from tempfile import TemporaryDirectory

from core.models import Product


class AdminUserDataActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="userdataadmin",
            email="userdataadmin@example.com",
            password="password",
        )
        self.temp_dir = TemporaryDirectory()
        self.user.data_path = self.temp_dir.name
        self.user.save(update_fields=["data_path"])
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("admin:core_product_changelist")
        self.factory = RequestFactory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_action_available_for_entity_models(self):
        request = self.factory.get(self.url)
        request.user = self.user
        actions = admin.site._registry[Product].get_actions(request)

        self.assertIn("toggle_selected_user_data", actions)

    def test_toggle_user_data_action_creates_and_removes_fixture(self):
        product = Product.objects.create(
            name="Sample Product", description="", renewal_period=30
        )
        fixture_path = (
            Path(self.temp_dir.name)
            / self.user.username
            / f"core_product_{product.pk}.json"
        )

        response = self.client.post(
            self.url,
            data={
                "action": "toggle_selected_user_data",
                "_selected_action": [product.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertTrue(product.is_user_data)
        self.assertTrue(fixture_path.exists())

        response = self.client.post(
            self.url,
            data={
                "action": "toggle_selected_user_data",
                "_selected_action": [product.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertFalse(product.is_user_data)
        self.assertFalse(fixture_path.exists())
