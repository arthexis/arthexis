from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase
from django.urls import reverse

from website.models import Application, SiteApplication


class MessageViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        site, _ = Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "Terminal"}
        )
        app = Application.objects.create(name="msg")
        SiteApplication.objects.create(site=site, application=app, path="/msg/", is_default=True)
        User = get_user_model()
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.user = User.objects.create_user("user", password="pw")
        self.url = reverse("msg:send")

    def test_staff_can_send_message(self):
        self.client.login(username="staff", password="pw")
        with patch("msg.views.notify") as mock_notify:
            resp = self.client.post(self.url, {"subject": "hi", "body": "there"})
        self.assertRedirects(resp, self.url)
        mock_notify.assert_called_once_with("hi", "there")

    def test_can_send_empty_subject_and_body(self):
        self.client.login(username="staff", password="pw")
        with patch("msg.views.notify") as mock_notify:
            resp = self.client.post(self.url, {"subject": "", "body": ""})
        self.assertRedirects(resp, self.url)
        mock_notify.assert_called_once_with("", "")

    def test_nonstaff_redirected(self):
        self.client.login(username="user", password="pw")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
