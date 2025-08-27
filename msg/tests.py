from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase
from django.urls import reverse

from website.models import Application, SiteApplication
from .models import Message


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


class MessageAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            "msg_admin", "admin@example.com", "pass"
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.m1 = Message.objects.create(subject="s1", body="b1")
        self.m2 = Message.objects.create(subject="s2", body="b2")
        self.url = reverse("admin:msg_message_changelist")

    @patch("msg.admin.notify")
    def test_send_messages_action(self, mock_notify):
        response = self.client.post(
            self.url,
            {
                "action": "send_messages",
                "_selected_action": [self.m1.pk, self.m2.pk],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_notify.call_count, 2)
        mock_notify.assert_any_call("s1", "b1")
        mock_notify.assert_any_call("s2", "b2")
        msgs = [m.message for m in response.wsgi_request._messages]
        self.assertIn("2 messages sent", msgs)
