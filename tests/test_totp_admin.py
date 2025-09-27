import time

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from django_otp.oath import TOTP

from teams.models import TOTPDevice


class TOTPDeviceAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )
        self.user = User.objects.create_user(
            username="enrolled", email="user@example.com", password="pass"
        )
        self.client.force_login(self.superuser)

    def _current_token(self, device):
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        totp.time = time.time()
        return f"{totp.token():0{device.digits}d}"

    def _post_action(self, device, token, follow=True):
        url = reverse("admin:teams_totpdevice_changelist")
        data = {
            "action": "calibrate_device",
            "token": token,
            "_selected_action": [str(device.pk)],
        }
        return self.client.post(url, data, follow=follow)

    def test_calibrate_action_accepts_valid_token(self):
        device = TOTPDevice.objects.create(user=self.user, name="Test device")
        response = self._post_action(device, self._current_token(device))

        self.assertEqual(response.status_code, 200)
        messages_list = [str(msg) for msg in get_messages(response.wsgi_request)]
        self.assertTrue(
            any("Token accepted" in message for message in messages_list),
            messages_list,
        )

        device.refresh_from_db()
        self.assertGreaterEqual(device.last_t, 0)
        self.assertIsNotNone(device.last_used_at)

    def test_calibrate_action_requires_token(self):
        device = TOTPDevice.objects.create(user=self.user, name="Test device")
        response = self._post_action(device, token="")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            any(
                "Enter the current authenticator code" in str(message)
                for message in get_messages(response.wsgi_request)
            )
        )

        device.refresh_from_db()
        self.assertEqual(device.last_t, -1)
        self.assertIsNone(device.last_used_at)
