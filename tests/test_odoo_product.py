from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import OdooProfile, User
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
