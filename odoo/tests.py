from django.test import Client, TestCase
from django.urls import reverse
from unittest.mock import patch

from .models import Instance


class OdooTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.instance = Instance.objects.create(
            name="Local",
            url="http://odoo.local",
            database="db",
            username="user",
            password="pwd",
        )

    @patch("xmlrpc.client.ServerProxy")
    def test_connection_success(self, mock_proxy):
        mock_srv = mock_proxy.return_value
        mock_srv.authenticate.return_value = 1
        response = self.client.post(reverse("odoo-test", args=[self.instance.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["detail"], "success")

    @patch("xmlrpc.client.ServerProxy")
    def test_connection_invalid(self, mock_proxy):
        mock_srv = mock_proxy.return_value
        mock_srv.authenticate.return_value = False
        response = self.client.post(reverse("odoo-test", args=[self.instance.pk]))
        self.assertEqual(response.status_code, 401)
