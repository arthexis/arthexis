from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import CustomerAccount, OdooProfile, User


class CustomerAccountImportFromOdooTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="customeradmin",
            email="admin@example.com",
            password="pwd",
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
        url = reverse("admin:core_customeraccount_import_from_odoo")
        response = self.client.get(url)
        self.assertContains(response, "Configure your CRM employee credentials")

    @patch.object(OdooProfile, "execute")
    def test_view_searches_customers(self, mock_execute):
        self._create_profile()
        mock_execute.return_value = [
            {
                "id": 7,
                "name": "ACME Corp",
                "email": "hello@example.com",
                "phone": "123",
                "mobile": "",
                "city": "CDMX",
                "country_id": [1, "Mexico"],
            }
        ]
        url = reverse("admin:core_customeraccount_import_from_odoo")
        response = self.client.post(url, {"name": "AC", "perform_search": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ACME Corp")
        mock_execute.assert_called_once_with(
            "res.partner",
            "search_read",
            [["customer_rank", ">", 0], ["name", "ilike", "AC"]],
            fields=["name", "email", "phone", "mobile", "city", "country_id"],
            limit=50,
        )

    @patch.object(OdooProfile, "execute")
    def test_import_creates_accounts_and_users(self, mock_execute):
        self._create_profile()
        mock_execute.return_value = [
            {
                "id": 9,
                "name": "Jane Doe",
                "email": "jane@example.com",
                "phone": "555-1234",
                "mobile": "555-5678",
                "city": "Austin",
                "country_id": [20, "USA"],
            }
        ]
        url = reverse("admin:core_customeraccount_import_from_odoo")
        response = self.client.post(
            url,
            {
                "perform_search": "1",
                "customer_ids": ["9"],
                "import_action": "import",
            },
        )
        account = CustomerAccount.objects.get()
        self.assertEqual(account.name, "JANE DOE")
        self.assertEqual(
            account.odoo_customer,
            {
                "id": 9,
                "name": "Jane Doe",
                "email": "jane@example.com",
                "phone": "555-1234",
                "mobile": "555-5678",
                "city": "Austin",
                "country": "USA",
            },
        )
        self.assertIsNotNone(account.user)
        self.assertEqual(account.user.email, "jane@example.com")
        self.assertRedirects(
            response, reverse("admin:core_customeraccount_changelist")
        )

    @patch.object(OdooProfile, "execute")
    def test_import_skips_existing_user_account(self, mock_execute):
        self._create_profile()
        user = User.objects.create_user("jane", email="jane@example.com")
        existing_account = CustomerAccount.objects.create(
            name="Existing Jane", user=user
        )
        mock_execute.return_value = [
            {
                "id": 10,
                "name": "Jane Doe",
                "email": "jane@example.com",
                "phone": "555-1234",
                "mobile": "555-5678",
                "city": "Austin",
                "country_id": [20, "USA"],
            }
        ]

        url = reverse("admin:core_customeraccount_import_from_odoo")
        response = self.client.post(
            url,
            {
                "perform_search": "1",
                "customer_ids": ["10"],
                "import_action": "import",
            },
        )

        existing_account.refresh_from_db()
        self.assertEqual(CustomerAccount.objects.count(), 1)
        self.assertEqual(existing_account.user, user)
        self.assertEqual(
            existing_account.odoo_customer,
            {
                "id": 10,
                "name": "Jane Doe",
                "email": "jane@example.com",
                "phone": "555-1234",
                "mobile": "555-5678",
                "city": "Austin",
                "country": "USA",
            },
        )
        self.assertRedirects(
            response, reverse("admin:core_customeraccount_changelist")
        )
