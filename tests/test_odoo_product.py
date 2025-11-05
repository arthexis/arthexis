from unittest.mock import patch

from django.contrib.admin.sites import site as default_site
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.auth.models import Permission
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
            "product.product",
            "search_read",
            fields=["name"],
            limit=50,
        )

    def test_product_admin_form_uses_widget(self):
        form = ProductAdminForm()
        self.assertIsInstance(form.fields["odoo_product"].widget, OdooProductWidget)


class ProductAdminActionTests(TestCase):
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

    def test_register_action_redirects(self):
        request = self._prepare_request(
            {"action": "register_from_odoo", "_selected_action": []}
        )
        response = self.admin.register_from_odoo(request, Product.objects.none())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, reverse("admin:core_product_register_from_odoo")
        )


class ProductAdminRegisterFromOdooTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="register-admin", email="reg@example.com", password="pwd"
        )
        self.client.force_login(self.user)

    def _create_profile(self):
        return OdooProfile.objects.create(
            user=self.user,
            host="http://odoo",
            database="db",
            username="api",
            password="secret",
            verified_on=timezone.now(),
            odoo_uid=5,
        )

    def test_view_requires_credentials(self):
        url = reverse("admin:core_product_register_from_odoo")
        response = self.client.get(url)
        self.assertContains(response, "Configure your CRM employee credentials")

    @patch.object(OdooProfile, "execute")
    def test_view_lists_products(self, mock_execute):
        self._create_profile()
        mock_execute.return_value = [
            {
                "id": 17,
                "name": "Service",
                "description_sale": "Annual service",
                "list_price": 199.0,
                "standard_price": 120.0,
            }
        ]
        url = reverse("admin:core_product_register_from_odoo")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Service")
        self.assertContains(response, "Annual service")
        self.assertContains(response, "199.0")
        mock_execute.assert_called_once_with(
            "product.product",
            "search_read",
            fields=[
                "name",
                "description_sale",
                "list_price",
                "standard_price",
            ],
            limit=0,
        )

    @patch.object(OdooProfile, "execute")
    def test_view_creates_product(self, mock_execute):
        self._create_profile()
        mock_execute.return_value = [
            {
                "id": 21,
                "name": "Managed Service",
                "description_sale": "Managed offering",
                "list_price": 99.0,
                "standard_price": 55.0,
            }
        ]
        url = reverse("admin:core_product_register_from_odoo")
        response = self.client.post(url, {"product_id": "21"})
        product = Product.objects.get()
        self.assertEqual(product.name, "Managed Service")
        self.assertEqual(product.description, "Managed offering")
        self.assertEqual(product.renewal_period, 30)
        self.assertEqual(product.odoo_product, {"id": 21, "name": "Managed Service"})
        self.assertRedirects(
            response, reverse("admin:core_product_change", args=[product.pk])
        )

    @patch.object(OdooProfile, "execute", side_effect=Exception("RPC failed"))
    def test_view_shows_debug_details_for_superuser(self, mock_execute):
        self._create_profile()
        url = reverse("admin:core_product_register_from_odoo")
        response = self.client.get(url)
        self.assertContains(response, "Unable to fetch products from Odoo.")
        self.assertContains(response, "Debug details")
        self.assertContains(response, "Exception: Exception: RPC failed")

    @patch.object(OdooProfile, "execute", side_effect=Exception("RPC failed"))
    def test_view_hides_debug_details_for_staff(self, mock_execute):
        staff = User.objects.create_user(
            username="staff-user",
            email="staff@example.com",
            password="pwd",
            is_staff=True,
        )
        permission = Permission.objects.get(codename="view_product")
        staff.user_permissions.add(permission)
        self.client.logout()
        self.client.force_login(staff)
        OdooProfile.objects.create(
            user=staff,
            host="http://odoo",
            database="db",
            username="api",
            password="secret",
            verified_on=timezone.now(),
            odoo_uid=7,
        )
        url = reverse("admin:core_product_register_from_odoo")
        response = self.client.get(url)
        self.assertContains(response, "Unable to fetch products from Odoo.")
        self.assertNotContains(response, "Debug details")
