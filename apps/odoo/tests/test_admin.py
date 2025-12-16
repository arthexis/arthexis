from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class OdooDeploymentAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )

    def test_discover_view_available(self):
        self.client.force_login(self.user)
        url = reverse("admin:odoo_odoodeployment_discover")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "admin/odoo/odoodeployment/discover.html"
        )
