from unittest.mock import patch

from django.contrib.admin.sites import site as default_site
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import OdooProfile, User


class OdooQuoteReportViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="quote-admin", email="quote@example.com", password="pwd"
        )
        self.client.force_login(self.user)

    def test_requires_verified_profile(self):
        response = self.client.get(reverse("odoo-quote-report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Configure and verify your CRM employee credentials",
        )

    @patch.object(OdooProfile, "execute")
    def test_renders_report_with_data(self, mock_execute):
        profile = OdooProfile.objects.create(
            user=self.user,
            host="http://odoo",
            database="db",
            username="api",
            password="secret",
            verified_on=timezone.now(),
            odoo_uid=7,
        )

        mock_execute.side_effect = [
            [{"id": 1, "name": "Standard"}],
            [
                {
                    "sale_order_template_id": (1, "Standard"),
                    "sale_order_template_id_count": 3,
                }
            ],
            [
                {
                    "name": "SO001",
                    "amount_total": 1250.5,
                    "partner_id": (5, "Acme"),
                    "activity_type_id": (9, "Call"),
                    "activity_summary": "Follow up",
                    "tag_ids": [4],
                    "create_date": "2024-01-02 12:00:00",
                    "currency_id": (3, "USD"),
                }
            ],
            [{"id": 4, "name": "Priority"}],
            [{"id": 3, "name": "USD", "symbol": "$"}],
            [
                {
                    "name": "Widget",
                    "default_code": "W-1",
                    "create_date": "2023-12-31 09:00:00",
                    "write_date": "2024-01-05 10:00:00",
                }
            ],
            [
                {
                    "name": "sale",
                    "shortdesc": "Sales",
                    "latest_version": "16.0.1",
                    "author": "Odoo",
                }
            ],
        ]

        response = self.client.get(reverse("odoo-quote-report"))
        self.assertEqual(response.status_code, 200)
        response.render()
        context = response.context_data
        self.assertEqual(context["template_stats"], [{"id": 1, "name": "Standard", "quote_count": 3}])
        self.assertEqual(len(context["quotes"]), 1)
        quote = context["quotes"][0]
        self.assertEqual(quote["name"], "SO001")
        self.assertEqual(quote["customer"], "Acme")
        self.assertEqual(quote["tags"], ["Priority"])
        self.assertEqual(quote["total_display"], "$1,250.50")
        self.assertIsNotNone(quote["create_date"])
        self.assertEqual(len(context["recent_products"]), 1)
        self.assertEqual(context["recent_products"][0]["name"], "Widget")
        self.assertEqual(len(context["installed_modules"]), 1)
        self.assertEqual(context["installed_modules"][0]["name"], "sale")
        self.assertEqual(mock_execute.call_count, 7)
        profile.refresh_from_db()
        self.assertTrue(profile.is_verified)


class OdooQuoteReportAdminActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="quote-action", email="action@example.com", password="pwd"
        )
        self.factory = RequestFactory()
        self.admin = default_site._registry[OdooProfile]

    def test_quote_report_action_redirects(self):
        request = self.factory.get("/admin/core/odooprofile/")
        request.user = self.user
        response = self.admin.generate_quote_report(request, OdooProfile.objects.none())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("odoo-quote-report"))
