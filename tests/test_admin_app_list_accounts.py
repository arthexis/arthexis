from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from pages.models import Application


class AdminAppListAccountsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_superuser(
            username="admin", password="pass", email="admin@example.com"
        )

    def test_accounts_app_included_when_not_ordered(self):
        Application.objects.all().delete()
        Application.objects.create(
            name="awg", description="Power module", order=1, is_seed_data=True
        )

        request = self.factory.get("/admin/")
        request.user = self.user

        app_list = site.get_app_list(request)
        app_order = {entry["app_label"]: entry["order"] for entry in app_list}

        self.assertIn("accounts", app_order)
        self.assertLess(app_order["awg"], app_order["accounts"])
