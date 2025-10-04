import time
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from django_otp.oath import TOTP

from teams.models import TOTPDevice
from teams.forms import TOTPDeviceAdminForm
from core.models import TOTPDeviceSettings


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
        data_root = Path(
            self.user.data_path or Path(settings.BASE_DIR) / "data"
        )
        data_root.mkdir(parents=True, exist_ok=True)
        self.user_data_dir = data_root / self.user.username
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        for path in self.user_data_dir.glob("*.json"):
            path.unlink()

    def tearDown(self):
        for path in self.user_data_dir.glob("*.json"):
            path.unlink()

    def _current_token(self, device):
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        totp.time = time.time()
        return f"{totp.token():0{device.digits}d}"

    def _change_form_data(self, device):
        url = reverse("admin:teams_totpdevice_change", args=[device.pk])
        form = TOTPDeviceAdminForm(instance=device)
        data = {}
        for name, field in form.fields.items():
            value = form.initial.get(name, field.initial)
            if isinstance(field, forms.ModelChoiceField):
                if value is None:
                    data[name] = ""
                else:
                    data[name] = str(getattr(value, "pk", value))
            elif isinstance(field, forms.MultipleChoiceField):
                if not value:
                    data[name] = []
                else:
                    data[name] = [str(v) for v in value]
            elif isinstance(field, forms.BooleanField):
                if value:
                    data[name] = "on"
            else:
                if value in (None, ""):
                    data[name] = ""
                else:
                    data[name] = str(value)
        return url, data

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

    def test_change_form_shows_user_data_checkboxes(self):
        device = TOTPDevice.objects.create(user=self.user, name="Test device")
        url = reverse("admin:teams_totpdevice_change", args=[device.pk])

        response = self.client.get(url)

        self.assertContains(response, 'name="_user_datum"')
        self.assertContains(response, 'name="_seed_datum"')

    def test_enabling_user_datum_persists_flags_and_fixture(self):
        device = TOTPDevice.objects.create(user=self.user, name="Samsung")
        url, data = self._change_form_data(device)
        data.update({"_user_datum": "on", "_seed_datum": "on", "_save": "Save"})

        response = self.client.post(url, data, follow=True)

        self.assertEqual(response.status_code, 200)
        device.refresh_from_db()
        settings_obj = TOTPDeviceSettings.objects.get(device=device)
        self.assertTrue(settings_obj.is_user_data)
        self.assertTrue(settings_obj.is_seed_data)

        fixture_path = self.user_data_dir / f"otp_totp_totpdevice_{device.pk}.json"
        self.assertTrue(fixture_path.exists())
        messages_list = [str(msg) for msg in get_messages(response.wsgi_request)]
        self.assertTrue(
            any(str(fixture_path) in message for message in messages_list),
            messages_list,
        )

    def test_disabling_user_datum_removes_fixture(self):
        device = TOTPDevice.objects.create(user=self.user, name="Samsung")
        url, data = self._change_form_data(device)
        data.update({"_user_datum": "on", "_save": "Save"})
        self.client.post(url, data, follow=True)

        fixture_path = self.user_data_dir / f"otp_totp_totpdevice_{device.pk}.json"
        self.assertTrue(fixture_path.exists())

        url, data = self._change_form_data(device)
        data.update({"_save": "Save"})
        response = self.client.post(url, data, follow=True)

        self.assertEqual(response.status_code, 200)
        device.refresh_from_db()
        self.assertFalse(
            TOTPDeviceSettings.objects.filter(
                device=device, is_user_data=True
            ).exists()
        )
        self.assertFalse(fixture_path.exists())
        messages_list = [str(msg) for msg in get_messages(response.wsgi_request)]
        self.assertFalse(
            any(str(fixture_path) in message for message in messages_list),
            messages_list,
        )
