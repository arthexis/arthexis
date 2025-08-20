import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.conf import settings
settings.ALLOWED_HOSTS=["testserver"]

from django.core.management import call_command
call_command("migrate", run_syncdb=True, verbosity=0)


from django.test import Client, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch

from .models import OdooInstance


class OdooTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", password="secret")
        self.client.force_login(self.user)
        os.environ.update(
            {
                "ODOO_URL": "http://odoo.local",
                "ODOO_DATABASE": "db",
                "ODOO_USERNAME": "user",
            }
        )
        self.instance = OdooInstance.objects.create(
            name="Local",
            url="[ODOO_URL]",
            database="[ODOO_DATABASE]",
            username="[ODOO_USERNAME]",
            password="pwd",
        )

    def test_fields_resolve_sigils(self):
        self.assertEqual(self.instance.url, "http://odoo.local")
        self.assertEqual(self.instance.database, "db")
        self.assertEqual(self.instance.username, "user")
        self.assertEqual(self.instance.password, "pwd")
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT password FROM integrator_odooinstance WHERE id = %s",
                [self.instance.pk],
            )
            raw = cursor.fetchone()[0]
        self.assertNotEqual(raw, "pwd")

    @patch("xmlrpc.client.ServerProxy")
    def test_connection_success(self, mock_proxy):
        mock_srv = mock_proxy.return_value
        mock_srv.authenticate.return_value = 1
        response = self.client.post(reverse("integrator:odoo-test", args=[self.instance.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["detail"], "success")

    @patch("xmlrpc.client.ServerProxy")
    def test_connection_invalid(self, mock_proxy):
        mock_srv = mock_proxy.return_value
        mock_srv.authenticate.return_value = False
        response = self.client.post(reverse("integrator:odoo-test", args=[self.instance.pk]))
        self.assertEqual(response.status_code, 401)


class OdooAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="odoo-admin",
            password="secret",
            email="admin@arthexis.com",
        )
        self.client.force_login(self.admin)
        os.environ.update(
            {
                "ODOO_URL": "http://odoo.local",
                "ODOO_DATABASE": "db",
                "ODOO_USERNAME": "user",
            }
        )
        self.instance = OdooInstance.objects.create(
            name="Local",
            url="[ODOO_URL]",
            database="[ODOO_DATABASE]",
            username="[ODOO_USERNAME]",
            password="pwd",
        )

    @patch("xmlrpc.client.ServerProxy")
    def test_admin_action(self, mock_proxy):
        mock_srv = mock_proxy.return_value
        mock_srv.authenticate.return_value = 1
        url = reverse("admin:integrator_odooinstance_changelist")
        resp = self.client.post(
            url,
            {
                "action": "test_connection",
                "_selected_action": [self.instance.pk],
            },
        )
        self.assertEqual(resp.status_code, 302)
        mock_proxy.assert_called()
        mock_srv.authenticate.assert_called_with(
            self.instance.database,
            self.instance.username,
            self.instance.password,
            {},
        )
