from unittest.mock import patch

from django.contrib.admin.sites import site as default_site
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import OdooProfile, Product, User
from core.admin import ProductAdminForm
from core.widgets import OdooProductWidget


class OdooProductTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="odooadmin", email="a@example.com", password="pwd"
        )
        OdooProfile.objects.create(
            user=self.user,
            host="http://test",
            database="db",
            username="odoo",
            password="secret",
            verified_on=timezone.now(),
            odoo_uid=1,
        )
        self.client.force_login(self.user)

    @patch.object(OdooProfile, "execute")
    def test_odoo_products_view(self, mock_exec):
        mock_exec.return_value = [{"id": 5, "name": "Prod"}]
        resp = self.client.get(reverse("odoo-products"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [{"id": 5, "name": "Prod"}])
        mock_exec.assert_called_once_with(
            "product.product", "search_read", [], {"fields": ["name"], "limit": 50}
        )

    def test_product_admin_form_uses_widget(self):
        form = ProductAdminForm()
        self.assertIsInstance(form.fields["odoo_product"].widget, OdooProductWidget)


class ProductAdminFetchWizardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="productadmin", email="admin@example.com", password="pwd"
        )
        self.factory = RequestFactory()
        # Use the registered admin instance so profile links are available.
        self.admin = default_site._registry[Product]

    def _prepare_request(self, data):
        request = self.factory.post("/admin/core/product/", data)
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)
        return request

    def test_wizard_requires_credentials(self):
        request = self._prepare_request(
            {"action": "fetch_odoo_product", "_selected_action": []}
        )
        response = self.admin.fetch_odoo_product(request, Product.objects.none())
        self.assertEqual(response.status_code, 200)
        content = response.render().content.decode()
        self.assertIn("Configure your Odoo employee credentials", content)
        profile_admin = self.admin._odoo_profile_admin()
        if profile_admin is not None:
            expected_url = profile_admin.get_my_profile_url(request)
            self.assertIn(expected_url, content)

    @patch.object(OdooProfile, "execute")
    def test_search_shows_results(self, mock_execute):
        OdooProfile.objects.create(
            user=self.user,
            host="http://odoo",
            database="db",
            username="api",
            password="secret",
            verified_on=timezone.now(),
            odoo_uid=5,
        )
        mock_execute.return_value = [
            {
                "id": 7,
                "name": "Widget",
                "default_code": "SKU-7",
                "barcode": "ABC",
                "description_sale": "Great widget",
            }
        ]
        request = self._prepare_request(
            {
                "action": "fetch_odoo_product",
                "_selected_action": [],
                "search": "yes",
                "name": "Wid",
                "renewal_period": "30",
            }
        )
        response = self.admin.fetch_odoo_product(request, Product.objects.none())
        self.assertEqual(response.status_code, 200)
        mock_execute.assert_called_once_with(
            "product.product",
            "search_read",
            [("name", "ilike", "Wid")],
            {
                "fields": [
                    "name",
                    "default_code",
                    "barcode",
                    "description_sale",
                ],
                "limit": 50,
            },
        )
        content = response.render().content.decode()
        self.assertIn("Widget", content)

    @patch.object(OdooProfile, "execute")
    def test_import_creates_product(self, mock_execute):
        OdooProfile.objects.create(
            user=self.user,
            host="http://odoo",
            database="db",
            username="api",
            password="secret",
            verified_on=timezone.now(),
            odoo_uid=5,
        )
        mock_execute.return_value = [
            {
                "id": 11,
                "name": "Imported",
                "default_code": "IMP-1",
                "barcode": "BRC",
                "description_sale": "Imported from Odoo",
            }
        ]
        request = self._prepare_request(
            {
                "action": "fetch_odoo_product",
                "_selected_action": [],
                "import": "yes",
                "product_id": "11",
                "renewal_period": "45",
            }
        )
        response = self.admin.fetch_odoo_product(request, Product.objects.none())
        self.assertEqual(response.status_code, 302)
        mock_execute.assert_called_once_with(
            "product.product",
            "search_read",
            [],
            {
                "fields": [
                    "name",
                    "default_code",
                    "barcode",
                    "description_sale",
                ],
                "limit": 50,
            },
        )
        product = Product.objects.get()
        self.assertEqual(product.name, "Imported")
        self.assertEqual(product.renewal_period, 45)
        self.assertEqual(product.odoo_product, {"id": 11, "name": "Imported"})
        self.assertEqual(
            response.url, reverse("admin:core_product_change", args=[product.pk])
        )
