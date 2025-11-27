import os
import json

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
import pytest

django.setup()

from django.test import (
    Client,
    RequestFactory,
    TestCase,
    SimpleTestCase,
    TransactionTestCase,
    override_settings,
)
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.shortcuts import resolve_url
from django.templatetags.static import static
from django.template import Context
from urllib.parse import quote
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.sites.models import Site
from django.contrib import admin, messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import DisallowedHost
from django.core.cache import cache
from django.db import connection
from django.db import migrations, models
import socket
from django.db import connection
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from pages import site_config
from pages.models import (
    Application,
    ChatMessage,
    ChatSession,
    DeveloperArticle,
    OdooChatBridge,
    WhatsAppChatBridge,
    Landing,
    Module,
    RoleLanding,
    SiteTemplate,
    SiteBadge,
    SiteProxy,
    Favorite,
    ViewHistory,
    LandingLead,
    UserManual,
    UserStory,
)
from django.http import FileResponse, HttpResponse

from pages.admin import (
    ApplicationAdmin,
    UserManualAdmin,
    UserStoryAdmin,
    ViewHistoryAdmin,
    log_viewer,
)
from pages.screenshot_specs import (
    ScreenshotSpec,
    ScreenshotSpecRunner,
    ScreenshotUnavailable,
    registry,
)
from pages.context_processors import nav_links
from pages.templatetags import admin_extras
from pages.middleware import LanguagePreferenceMiddleware
from django.apps import apps as django_apps
from config.asgi import application
from config.middleware import SiteHttpsRedirectMiddleware
from core import mailer
from core.admin import ProfileAdminMixin
from pages.odoo import forward_chat_message
from pages.whatsapp import forward_chat_message as forward_whatsapp_message
from core.models import (
    AdminCommandResult,
    AdminHistory,
    ClientReport,
    CustomerAccount,
    InviteLead,
    Package,
    PackageRelease,
    OdooProfile,
    Reference,
    RFID,
    PasskeyCredential,
    ReleaseManager,
    SecurityGroup,
    GoogleCalendarProfile,
    TOTPDeviceSettings,
)
from ocpp.models import Charger, ChargerConfiguration, CPFirmware
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
import base64
import json
import tempfile
import shutil
from datetime import timedelta
from io import StringIO
from django.conf import settings
from django.utils import timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch
from types import SimpleNamespace
from django.core.management import call_command
from django.core.management.base import BaseCommand
import re
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from datetime import (
    date,
    datetime,
    time as datetime_time,
    timedelta,
    timezone as datetime_timezone,
)
from django.core import mail
from django.utils import timezone
from django.utils.text import slugify
from django.utils import translation
from django.utils.translation import gettext
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from core.backends import TOTP_DEVICE_NAME
from pages.views import PASSKEY_LOGIN_SESSION_KEY, PASSKEY_REGISTRATION_SESSION_KEY
import time
import asyncio

from nodes.models import (
    Node,
    ContentSample,
    NodeRole,
    NodeFeature,
    NodeFeatureAssignment,
    NetMessage,
    BadgeCounter,
)
from teams.models import EmailOutbox
from django.contrib.auth.models import AnonymousUser

class LoginViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="staff", password="pwd", is_staff=True
        )
        self.user = User.objects.create_user(username="user", password="pwd")
        Site.objects.update_or_create(id=1, defaults={"name": "Terminal"})

    def _enable_rfid_scanner(self):
        node, _ = Node.objects.get_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "local-node"},
        )
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rfid-scanner", defaults={"display": "RFID Scanner"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        return node

    def _create_totp_device(self, *, allow_without_password=False, user=None):
        owner = user or self.staff
        device = TOTPDevice.objects.create(
            user=owner,
            name=TOTP_DEVICE_NAME,
            confirmed=True,
        )
        if allow_without_password:
            TOTPDeviceSettings.objects.create(
                device=device,
                allow_without_password=True,
            )
        return device

    def _current_token(self, device):
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        totp.time = time.time()
        return f"{totp.token():0{device.digits}d}"

    def test_login_link_in_navbar(self):
        resp = self.client.get(reverse("pages:index"))
        login_url = resolve_url(settings.LOGIN_URL)
        self.assertContains(resp, f'href="{login_url}"')

    @override_settings(LOGIN_URL="/staff/login/")
    def test_login_link_uses_configured_login_url(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'href="/staff/login/"')

    def test_login_page_shows_authenticator_toggle(self):
        resp = self.client.get(reverse("pages:login"))
        self.assertContains(resp, "Use Authenticator app")

    def test_login_page_hides_passkey_button_when_disabled(self):
        resp = self.client.get(reverse("pages:login"))
        self.assertNotContains(resp, "Use a passkey")

    @override_settings(PASSKEY_LOGIN_ENABLED=True)
    def test_login_page_shows_passkey_button_when_enabled(self):
        resp = self.client.get(reverse("pages:login"))
        self.assertContains(resp, "Use a passkey")

    def test_passkey_login_options_sets_session_challenge(self):
        response = self.client.post(reverse("pages:passkey-login-options"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("publicKey", payload)
        session = self.client.session
        self.assertIn(PASSKEY_LOGIN_SESSION_KEY, session)
        self.assertTrue(session[PASSKEY_LOGIN_SESSION_KEY])

    @patch("pages.views.passkeys.verify_authentication_response")
    def test_passkey_login_verify_authenticates_user(self, mock_verify):
        passkey = PasskeyCredential.objects.create(
            user=self.staff,
            name="Primary",
            credential_id="cred-1",
            public_key=b"public",
            sign_count=1,
            user_handle="user-handle",
        )
        session = self.client.session
        session[PASSKEY_LOGIN_SESSION_KEY] = "expected-challenge"
        session.save()

        mock_verify.return_value = SimpleNamespace(new_sign_count=5)

        response = self.client.post(
            reverse("pages:passkey-login-verify"),
            data=json.dumps(
                {
                    "credential": {
                        "id": passkey.credential_id,
                        "type": "public-key",
                        "response": {
                            "clientDataJSON": "YQ",
                            "authenticatorData": "Yg",
                            "signature": "Yw",
                            "userHandle": passkey.user_handle,
                        },
                    },
                    "next": "",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect"], reverse("admin:index"))
        session = self.client.session
        self.assertEqual(session.get("_auth_user_id"), str(self.staff.pk))
        passkey.refresh_from_db()
        self.assertEqual(passkey.sign_count, 5)
        self.assertIsNotNone(passkey.last_used_at)
        self.assertNotIn(PASSKEY_LOGIN_SESSION_KEY, session)

    @patch("pages.views.passkeys.verify_registration_response")
    def test_passkey_registration_creates_credential(self, mock_verify):
        self.client.force_login(self.staff)
        options_response = self.client.post(
            reverse("pages:passkey-register-options"),
            data=json.dumps({"name": "Laptop"}),
            content_type="application/json",
        )
        self.assertEqual(options_response.status_code, 200)
        session = self.client.session
        self.assertIn(PASSKEY_REGISTRATION_SESSION_KEY, session)
        session_data = session[PASSKEY_REGISTRATION_SESSION_KEY]
        self.assertEqual(session_data["name"], "Laptop")

        mock_verify.return_value = SimpleNamespace(
            credential_id=b"credential",
            credential_public_key=b"public-key",
            sign_count=2,
        )

        verify_response = self.client.post(
            reverse("pages:passkey-register-verify"),
            data=json.dumps(
                {
                    "credential": {
                        "id": "credential",
                        "type": "public-key",
                        "response": {
                            "clientDataJSON": "YQ",
                            "attestationObject": "Yg",
                            "userHandle": session_data["user_handle"],
                        },
                    }
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(verify_response.status_code, 200)
        payload = verify_response.json()
        self.assertEqual(payload["name"], "Laptop")
        passkey = PasskeyCredential.objects.get(pk=payload["id"])
        self.assertEqual(passkey.name, "Laptop")
        self.assertEqual(passkey.sign_count, 2)
        self.assertNotIn(PASSKEY_REGISTRATION_SESSION_KEY, self.client.session)

    def test_passkey_delete_removes_credential(self):
        self.client.force_login(self.staff)
        passkey = PasskeyCredential.objects.create(
            user=self.staff,
            name="Tablet",
            credential_id="tablet-cred",
            public_key=b"data",
            sign_count=0,
            user_handle="tablet-handle",
        )
        response = self.client.post(
            reverse("pages:passkey-delete", args=[passkey.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PasskeyCredential.objects.filter(pk=passkey.pk).exists())

    def test_cp_simulator_redirect_shows_restricted_message(self):
        simulator_path = reverse("cp-simulator")
        resp = self.client.get(f"{reverse('pages:login')}?next={simulator_path}")
        self.assertContains(
            resp,
            "This page is reserved for members only. Please log in to continue.",
        )

    def test_staff_login_redirects_admin(self):
        resp = self.client.post(
            reverse("pages:login"),
            {"username": "staff", "password": "pwd"},
        )
        self.assertRedirects(resp, reverse("admin:index"))

    def test_login_with_authenticator_code(self):
        device = self._create_totp_device()
        token = self._current_token(device)

        resp = self.client.post(
            reverse("pages:login"),
            {
                "username": "staff",
                "auth_method": "otp",
                "otp_token": token,
                "password": "pwd",
            },
        )

        self.assertRedirects(resp, reverse("admin:index"))
        session = self.client.session
        self.assertIn(DEVICE_ID_SESSION_KEY, session)
        self.assertEqual(session[DEVICE_ID_SESSION_KEY], device.persistent_id)

    def test_login_with_invalid_authenticator_code(self):
        self._create_totp_device()

        resp = self.client.post(
            reverse("pages:login"),
            {
                "username": "staff",
                "auth_method": "otp",
                "otp_token": "000000",
                "password": "pwd",
            },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "authenticator code is invalid", status_code=200)

    def test_login_with_authenticator_requires_password_when_flag_disabled(self):
        device = self._create_totp_device()
        token = self._current_token(device)

        resp = self.client.post(
            reverse("pages:login"),
            {
                "username": "staff",
                "auth_method": "otp",
                "otp_token": token,
            },
        )

        self.assertContains(resp, "Enter your password.", status_code=200)

    def test_login_with_authenticator_without_password_when_allowed(self):
        device = self._create_totp_device(allow_without_password=True)
        token = self._current_token(device)

        resp = self.client.post(
            reverse("pages:login"),
            {
                "username": "staff",
                "auth_method": "otp",
                "otp_token": token,
            },
        )

        self.assertRedirects(resp, reverse("admin:index"))

    def test_login_with_passwordless_device_when_another_requires_password(self):
        self._create_totp_device()
        passwordless_device = self._create_totp_device(allow_without_password=True)
        token = self._current_token(passwordless_device)

        resp = self.client.post(
            reverse("pages:login"),
            {
                "username": "staff",
                "auth_method": "otp",
                "otp_token": token,
            },
        )

        self.assertRedirects(resp, reverse("admin:index"))

    def test_login_with_required_password_device_when_optional_password_shown(self):
        required_device = self._create_totp_device()
        self._create_totp_device(allow_without_password=True)
        token = self._current_token(required_device)

        resp = self.client.post(
            reverse("pages:login"),
            {
                "username": "staff",
                "auth_method": "otp",
                "otp_token": token,
            },
        )

        self.assertContains(resp, "Enter your password.", status_code=200)

    def test_authenticator_login_check_requires_username(self):
        resp = self.client.post(reverse("pages:authenticator-login-check"), {})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("username", resp.json().get("error", "").lower())

    def test_authenticator_login_check_reports_password_requirement(self):
        self._create_totp_device()

        resp = self.client.post(
            reverse("pages:authenticator-login-check"),
            {"username": "staff"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["requires_password"])

    def test_authenticator_login_check_respects_passwordless_flag(self):
        self._create_totp_device(allow_without_password=True)

        resp = self.client.post(
            reverse("pages:authenticator-login-check"),
            {"username": "staff"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["requires_password"])

    def test_authenticator_login_check_marks_password_optional_when_mixed(self):
        self._create_totp_device()
        self._create_totp_device(allow_without_password=True)

        resp = self.client.post(
            reverse("pages:authenticator-login-check"),
            {"username": "staff"},
        )

        payload = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(payload["requires_password"])
        self.assertTrue(payload["password_optional"])

    def test_login_accepts_group_assigned_authenticator(self):
        group = SecurityGroup.objects.create(name="Operators")
        self.user.groups.add(group)
        device_owner = get_user_model().objects.create_user("totp-owner", password="pwd")
        device = self._create_totp_device(
            allow_without_password=True, user=device_owner
        )
        settings_obj = device.custom_settings
        settings_obj.security_group = group
        settings_obj.save(update_fields=["security_group", "allow_without_password"])
        token = self._current_token(device)

        resp = self.client.post(
            reverse("pages:login"),
            {
                "username": "user",
                "auth_method": "otp",
                "otp_token": token,
            },
        )

        self.assertRedirects(resp, "/")
        session = self.client.session
        self.assertEqual(session.get("_auth_user_id"), str(self.user.pk))
        session_device_id = session.get(DEVICE_ID_SESSION_KEY)
        self.assertIsNotNone(session_device_id)
        session_device = TOTPDevice.objects.get(
            pk=int(session_device_id.split("/")[-1])
        )
        self.assertEqual(session_device.user, self.user)
        self.assertEqual(session_device.key, device.key)

    def test_already_logged_in_staff_redirects(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("pages:login"))
        self.assertRedirects(resp, reverse("admin:index"))

    def test_login_check_allows_authenticated_user(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("pages:login") + "?check=1")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<input type="hidden" name="check" value="1">', html=True)
        self.assertContains(resp, 'value="staff"')
        self.assertContains(resp, 'readonly aria-readonly="true"')

    def test_regular_user_redirects_next(self):
        resp = self.client.post(
            reverse("pages:login") + "?next=/nodes/list/",
            {"username": "user", "password": "pwd"},
        )
        self.assertRedirects(resp, "/nodes/list/")

    def test_admin_password_change_includes_test_password_link(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("admin:password_change"))
        test_url = f"{reverse('pages:login')}?check=1"

        self.assertContains(resp, f'href="{test_url}"')
        self.assertContains(resp, "Test my current password")

    def test_login_page_shows_rfid_link_when_feature_enabled(self):
        self._enable_rfid_scanner()
        resp = self.client.get(reverse("pages:login"))
        self.assertContains(resp, reverse("pages:rfid-login"))

    def test_login_page_detects_rfid_lock_without_mac_address(self):
        Node.objects.all().delete()
        NodeFeature.objects.get_or_create(
            slug="rfid-scanner", defaults={"display": "RFID Scanner"}
        )
        with tempfile.TemporaryDirectory() as tempdir:
            locks_dir = Path(tempdir) / "locks"
            locks_dir.mkdir()
            (locks_dir / "rfid.lck").touch()
            Node.objects.create(
                hostname="local-node",
                base_path=tempdir,
                current_relation=Node.Relation.SELF,
                mac_address=None,
            )

            resp = self.client.get(reverse("pages:login"))

        self.assertContains(resp, reverse("pages:rfid-login"))

    def test_rfid_login_page_requires_feature(self):
        resp = self.client.get(reverse("pages:rfid-login"))
        self.assertEqual(resp.status_code, 404)

    def test_rfid_login_page_redirects_authenticated_user(self):
        self._enable_rfid_scanner()
        self.client.force_login(self.user)
        resp = self.client.get(reverse("pages:rfid-login"))
        self.assertRedirects(resp, "/")

    def test_rfid_login_page_includes_scan_url(self):
        self._enable_rfid_scanner()
        resp = self.client.get(reverse("pages:rfid-login"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["login_api_url"], reverse("rfid-login"))
        self.assertEqual(resp.context["scan_api_url"], reverse("rfid-scan-next"))

    def test_homepage_excludes_version_banner_for_anonymous(self):
        response = self.client.get(reverse("pages:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "__versionCheckInitialized")

    def test_homepage_includes_version_banner_for_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("pages:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "__versionCheckInitialized")


class AdminTemplateVersionBannerTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="admin-staff", password="pwd", is_staff=True
        )

    def test_admin_login_excludes_version_banner_for_anonymous(self):
        response = self.client.get(reverse("admin:login"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "__versionCheckInitialized")

    def test_admin_dashboard_includes_version_banner_for_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "__versionCheckInitialized")

    def test_staff_redirects_next_when_specified(self):
        resp = self.client.post(
            reverse("pages:login") + "?next=/nodes/list/",
            {"username": "staff", "password": "pwd"},
        )
        self.assertRedirects(resp, "/nodes/list/")



    @override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend")
    def test_login_page_hides_request_link_without_email_backend(self):
        resp = self.client.get(reverse("pages:login"))
        self.assertFalse(resp.context["can_request_invite"])
        self.assertNotContains(resp, reverse("pages:request-invite"))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend")
    def test_login_page_shows_request_link_when_outbox_configured(self):
        EmailOutbox.objects.create(host="smtp.example.com")
        resp = self.client.get(reverse("pages:login"))
        self.assertTrue(resp.context["can_request_invite"])
        self.assertContains(resp, reverse("pages:request-invite"))


    @override_settings(ALLOWED_HOSTS=["gway-qk32000"])
    def test_login_allows_forwarded_https_origin(self):
        secure_client = Client(enforce_csrf_checks=True)
        login_url = reverse("pages:login")
        response = secure_client.get(login_url, HTTP_HOST="gway-qk32000")
        csrf_cookie = response.cookies["csrftoken"].value
        submit = secure_client.post(
            login_url,
            {
                "username": "staff",
                "password": "pwd",
                "csrfmiddlewaretoken": csrf_cookie,
            },
            HTTP_HOST="gway-qk32000",
            HTTP_ORIGIN="https://gway-qk32000",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_REFERER="https://gway-qk32000/login/",
        )
        self.assertRedirects(submit, reverse("admin:index"))

    @override_settings(ALLOWED_HOSTS=["10.42.0.0/16", "gway-qk32000"])
    def test_login_allows_forwarded_origin_with_private_host_header(self):
        secure_client = Client(enforce_csrf_checks=True)
        login_url = reverse("pages:login")
        response = secure_client.get(login_url, HTTP_HOST="10.42.0.2")
        csrf_cookie = response.cookies["csrftoken"].value
        submit = secure_client.post(
            login_url,
            {
                "username": "staff",
                "password": "pwd",
                "csrfmiddlewaretoken": csrf_cookie,
            },
            HTTP_HOST="10.42.0.2",
            HTTP_ORIGIN="https://gway-qk32000",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_FORWARDED_HOST="gway-qk32000",
            HTTP_REFERER="https://gway-qk32000/login/",
        )
        self.assertRedirects(submit, reverse("admin:index"))

    @override_settings(ALLOWED_HOSTS=["10.42.0.0/16", "gway-qk32000"])
    def test_login_allows_forwarded_header_host_and_proto(self):
        secure_client = Client(enforce_csrf_checks=True)
        login_url = reverse("pages:login")
        response = secure_client.get(login_url, HTTP_HOST="10.42.0.2")
        csrf_cookie = response.cookies["csrftoken"].value
        submit = secure_client.post(
            login_url,
            {
                "username": "staff",
                "password": "pwd",
                "csrfmiddlewaretoken": csrf_cookie,
            },
            HTTP_HOST="10.42.0.2",
            HTTP_ORIGIN="https://gway-qk32000",
            HTTP_FORWARDED="proto=https;host=gway-qk32000",
            HTTP_REFERER="https://gway-qk32000/login/",
        )
        self.assertRedirects(submit, reverse("admin:index"))

    @override_settings(ALLOWED_HOSTS=["10.42.0.0/16", "gway-qk32000"])
    def test_login_allows_forwarded_referer_without_origin(self):
        secure_client = Client(enforce_csrf_checks=True)
        login_url = reverse("pages:login")
        response = secure_client.get(login_url, HTTP_HOST="10.42.0.2")
        csrf_cookie = response.cookies["csrftoken"].value
        submit = secure_client.post(
            login_url,
            {
                "username": "staff",
                "password": "pwd",
                "csrfmiddlewaretoken": csrf_cookie,
            },
            HTTP_HOST="10.42.0.2",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_FORWARDED_HOST="gway-qk32000",
            HTTP_REFERER="https://gway-qk32000/login/",
        )
        self.assertRedirects(submit, reverse("admin:index"))

    @override_settings(ALLOWED_HOSTS=["gway-qk32000"])
    def test_login_allows_forwarded_origin_with_explicit_port(self):
        secure_client = Client(enforce_csrf_checks=True)
        login_url = reverse("pages:login")
        response = secure_client.get(login_url, HTTP_HOST="gway-qk32000")
        csrf_cookie = response.cookies["csrftoken"].value
        submit = secure_client.post(
            login_url,
            {
                "username": "staff",
                "password": "pwd",
                "csrfmiddlewaretoken": csrf_cookie,
            },
            HTTP_HOST="gway-qk32000",
            HTTP_ORIGIN="https://gway-qk32000:4443",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_FORWARDED_HOST="gway-qk32000:4443",
            HTTP_REFERER="https://gway-qk32000:4443/login/",
        )
        self.assertRedirects(submit, reverse("admin:index"))


class AuthenticatorSetupTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="staffer", password="pwd", is_staff=True
        )
        Site.objects.update_or_create(id=1, defaults={"name": "Terminal"})
        self.client.force_login(self.staff)

    def _current_token(self, device):
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        totp.time = time.time()
        return f"{totp.token():0{device.digits}d}"

    def test_generate_creates_pending_device(self):
        resp = self.client.post(
            reverse("pages:authenticator-setup"), {"action": "generate"}
        )
        self.assertRedirects(resp, reverse("pages:authenticator-setup"))
        device = TOTPDevice.objects.get(user=self.staff)
        self.assertFalse(device.confirmed)
        self.assertEqual(device.name, TOTP_DEVICE_NAME)

    def test_device_config_url_includes_issuer_prefix(self):
        self.client.post(reverse("pages:authenticator-setup"), {"action": "generate"})
        device = TOTPDevice.objects.get(user=self.staff)
        config_url = device.config_url
        label = quote(f"{settings.OTP_TOTP_ISSUER}:{self.staff.username}")
        self.assertIn(label, config_url)
        self.assertIn(f"issuer={quote(settings.OTP_TOTP_ISSUER)}", config_url)

    def test_device_config_url_uses_custom_issuer_when_available(self):
        self.client.post(reverse("pages:authenticator-setup"), {"action": "generate"})
        device = TOTPDevice.objects.get(user=self.staff)
        TOTPDeviceSettings.objects.create(device=device, issuer="Custom Co")
        config_url = device.config_url
        quoted_issuer = quote("Custom Co")
        self.assertIn(quoted_issuer, config_url)
        self.assertIn(f"issuer={quoted_issuer}", config_url)

    def test_pending_device_context_includes_qr(self):
        self.client.post(reverse("pages:authenticator-setup"), {"action": "generate"})
        resp = self.client.get(reverse("pages:authenticator-setup"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["qr_data_uri"].startswith("data:image/png;base64,"))
        self.assertTrue(resp.context["manual_key"])

    def test_confirm_pending_device(self):
        self.client.post(reverse("pages:authenticator-setup"), {"action": "generate"})
        device = TOTPDevice.objects.get(user=self.staff)
        token = self._current_token(device)
        resp = self.client.post(
            reverse("pages:authenticator-setup"),
            {"action": "confirm", "token": token},
        )
        self.assertRedirects(resp, reverse("pages:authenticator-setup"))
        device.refresh_from_db()
        self.assertTrue(device.confirmed)

    def test_remove_device(self):
        self.client.post(reverse("pages:authenticator-setup"), {"action": "generate"})
        device = TOTPDevice.objects.get(user=self.staff)
        token = self._current_token(device)
        self.client.post(
            reverse("pages:authenticator-setup"),
            {"action": "confirm", "token": token},
        )
        resp = self.client.post(
            reverse("pages:authenticator-setup"), {"action": "remove"}
        )
        self.assertRedirects(resp, reverse("pages:authenticator-setup"))
        self.assertFalse(TOTPDevice.objects.filter(user=self.staff).exists())

@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InvitationTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="invited",
            email="invite@example.com",
            is_active=False,
        )
        self.user.set_unusable_password()
        self.user.save()
        Site.objects.update_or_create(id=1, defaults={"name": "Terminal"})

    def test_login_page_has_request_link(self):
        resp = self.client.get(reverse("pages:login"))
        self.assertContains(resp, reverse("pages:request-invite"))

    def test_request_invite_sets_csrf_cookie(self):
        resp = self.client.get(reverse("pages:request-invite"))
        self.assertIn("csrftoken", resp.cookies)

    def test_request_invite_allows_post_without_csrf(self):
        client = Client(enforce_csrf_checks=True)
        resp = client.post(
            reverse("pages:request-invite"), {"email": "invite@example.com"}
        )
        self.assertEqual(resp.status_code, 200)

    def test_invitation_flow(self):
        resp = self.client.post(
            reverse("pages:request-invite"), {"email": "invite@example.com"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"http://testserver[\S]+", mail.outbox[0].body).group(0)
        resp = self.client.get(link)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(link)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertIn("_auth_user_id", self.client.session)

    def test_request_invite_handles_email_errors(self):
        with patch("pages.views.mailer.send", side_effect=Exception("fail")):
            resp = self.client.post(
                reverse("pages:request-invite"), {"email": "invite@example.com"}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "If the email exists, an invitation has been sent.")
        lead = InviteLead.objects.get()
        self.assertIsNone(lead.sent_on)
        self.assertIn("fail", lead.error)
        self.assertIn("email service", lead.error)
        self.assertEqual(len(mail.outbox), 0)

    def test_request_invite_records_send_time(self):
        resp = self.client.post(
            reverse("pages:request-invite"), {"email": "invite@example.com"}
        )
        self.assertEqual(resp.status_code, 200)
        lead = InviteLead.objects.get()
        self.assertIsNotNone(lead.sent_on)
        self.assertEqual(lead.error, "")
        self.assertEqual(len(mail.outbox), 1)

    def test_request_invite_creates_lead_with_comment(self):
        resp = self.client.post(
            reverse("pages:request-invite"),
            {"email": "new@example.com", "comment": "Hello"},
        )
        self.assertEqual(resp.status_code, 200)
        lead = InviteLead.objects.get()
        self.assertEqual(lead.email, "new@example.com")
        self.assertEqual(lead.comment, "Hello")
        self.assertIsNone(lead.sent_on)
        self.assertEqual(lead.error, "")
        self.assertEqual(lead.mac_address, "")
        self.assertEqual(len(mail.outbox), 0)

    def test_request_invite_uses_original_referer(self):
        InviteLead.objects.all().delete()
        self.client.get(
            reverse("pages:index"),
            HTTP_REFERER="https://campaign.example/landing",
        )

        resp = self.client.post(
            reverse("pages:request-invite"),
            {"email": "origin@example.com"},
            HTTP_REFERER="http://testserver/pages/request-invite/",
        )

        self.assertEqual(resp.status_code, 200)
        lead = InviteLead.objects.get()
        self.assertEqual(lead.referer, "https://campaign.example/landing")

    def test_request_invite_falls_back_to_send_mail(self):
        node = Node.objects.create(
            hostname="local", address="127.0.0.1", mac_address="00:11:22:33:44:55"
        )
        with (
            patch("pages.views.Node.get_local", return_value=node),
            patch.object(
                node, "send_mail", side_effect=Exception("node fail")
            ) as node_send,
            patch("pages.views.mailer.send", wraps=mailer.send) as fallback,
        ):
            resp = self.client.post(
                reverse("pages:request-invite"), {"email": "invite@example.com"}
            )
        self.assertEqual(resp.status_code, 200)
        lead = InviteLead.objects.get()
        self.assertIsNotNone(lead.sent_on)
        self.assertIn("node fail", lead.error)
        self.assertIn("default mail backend", lead.error)
        self.assertTrue(node_send.called)
        self.assertTrue(fallback.called)
        self.assertEqual(len(mail.outbox), 1)

    @patch(
        "pages.views.public_wifi.resolve_mac_address",
        return_value="aa:bb:cc:dd:ee:ff",
    )
    def test_request_invite_records_mac_address(self, mock_resolve):
        resp = self.client.post(
            reverse("pages:request-invite"), {"email": "invite@example.com"}
        )
        self.assertEqual(resp.status_code, 200)
        lead = InviteLead.objects.get()
        self.assertEqual(lead.mac_address, "aa:bb:cc:dd:ee:ff")

    @pytest.mark.feature("ap-router")
    @patch("pages.views.public_wifi.grant_public_access")
    @patch(
        "pages.views.public_wifi.resolve_mac_address",
        return_value="aa:bb:cc:dd:ee:ff",
    )
    def test_invitation_login_grants_public_wifi_access(self, mock_resolve, mock_grant):
        control_role, _ = NodeRole.objects.get_or_create(name="Control")
        feature = NodeFeature.objects.create(slug="ap-router", display="AP Router")
        feature.roles.add(control_role)
        node = Node.objects.create(
            hostname="control",
            address="127.0.0.1",
            mac_address=Node.get_current_mac(),
            role=control_role,
        )
        NodeFeatureAssignment.objects.create(node=node, feature=feature)
        with patch("pages.views.Node.get_local", return_value=node):
            resp = self.client.post(
                reverse("pages:request-invite"), {"email": "invite@example.com"}
            )
        self.assertEqual(resp.status_code, 200)
        link = re.search(r"http://testserver[\S]+", mail.outbox[0].body).group(0)
        with patch("pages.views.Node.get_local", return_value=node):
            resp = self.client.post(link)
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        mock_grant.assert_called_once_with(self.user, "aa:bb:cc:dd:ee:ff")


class NavbarBrandTests(TestCase):
    def setUp(self):
        self.client = Client()
        Site.objects.update_or_create(
            id=1, defaults={"name": "Terminal", "domain": "testserver"}
        )

    def test_site_name_displayed_when_known(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, '<a class="navbar-brand" href="/">Terminal</a>')

    def test_default_brand_when_unknown(self):
        Site.objects.filter(id=1).update(domain="example.com")
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, '<a class="navbar-brand" href="/">Arthexis</a>')

    @override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"])
    def test_brand_uses_role_name_when_site_name_blank(self):
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": role,
            },
        )
        Site.objects.filter(id=1).update(name="", domain="127.0.0.1")
        resp = self.client.get(reverse("pages:index"), HTTP_HOST="127.0.0.1")
        self.assertEqual(resp.context["badge_site_name"], "Terminal")
        self.assertContains(resp, '<a class="navbar-brand" href="/">Terminal</a>')


class AdminBadgesTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="badge-admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )
        from nodes.models import Node

        self.node_hostname = "otherhost"
        self.node = Node.objects.create(
            hostname=self.node_hostname,
            address=socket.gethostbyname(socket.gethostname()),
        )

    def test_badges_show_site_and_node(self):
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "SITE: test")
        self.assertContains(resp, f"NODE: {self.node_hostname}")

    def test_badges_show_node_role(self):
        from nodes.models import NodeRole

        role = NodeRole.objects.create(name="Dev")
        self.node.role = role
        self.node.save()
        resp = self.client.get(reverse("admin:index"))
        role_list = reverse("admin:nodes_noderole_changelist")
        role_change = reverse("admin:nodes_noderole_change", args=[role.pk])
        self.assertContains(resp, "ROLE: Dev")
        self.assertContains(resp, f'href="{role_list}"')
        self.assertContains(resp, f'href="{role_change}"')

    def test_badges_warn_when_node_missing(self):
        from nodes.models import Node

        Node.objects.all().delete()
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "NODE: Unknown")
        self.assertContains(resp, "badge-unknown")
        self.assertContains(resp, "#6c757d")

    def test_badges_link_to_admin(self):
        resp = self.client.get(reverse("admin:index"))
        site_list = reverse("admin:pages_siteproxy_changelist")
        site_change = reverse("admin:pages_siteproxy_change", args=[1])
        node_list = reverse("admin:nodes_node_changelist")
        node_change = reverse("admin:nodes_node_change", args=[self.node.pk])
        self.assertContains(resp, f'href="{site_list}"')
        self.assertContains(resp, f'href="{site_change}"')
        self.assertContains(resp, f'href="{node_list}"')
        self.assertContains(resp, f'href="{node_change}"')

    def test_badge_colors_use_standard_palette(self):
        site = Site.objects.get(pk=1)
        badge, _ = SiteBadge.objects.get_or_create(site=site)
        badge.badge_color = "#ff0000"
        badge.save(update_fields=["badge_color"])
        self.node.badge_color = "#123456"
        self.node.save(update_fields=["badge_color"])

        resp = self.client.get(reverse("admin:index"))

        self.assertNotContains(resp, "#ff0000")
        self.assertNotContains(resp, "#123456")
        self.assertContains(resp, 'style="background-color: #28a745;"', 2)


class SiteProxyAdminPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="siteproxy_viewer", password="pwd", is_staff=True
        )
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )

    def test_staff_without_permissions_cannot_load_change_list(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("admin:pages_siteproxy_changelist"))
        self.assertEqual(resp.status_code, 403)

    def test_staff_with_pages_siteproxy_permission_can_load_change_list(self):
        perm = Permission.objects.get(codename="view_siteproxy")
        self.user.user_permissions.add(perm)
        self.client.force_login(self.user)
        resp = self.client.get(reverse("admin:pages_siteproxy_changelist"))
        self.assertEqual(resp.status_code, 200)


class AdminDashboardAppListTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="dashboard_admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )
        self.locks_dir = Path(settings.BASE_DIR) / "locks"
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self.celery_lock = self.locks_dir / "celery.lck"
        if self.celery_lock.exists():
            self.celery_lock.unlink()
        self.addCleanup(self._remove_celery_lock)
        self.node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": socket.gethostbyname(socket.gethostname()),
                "base_path": settings.BASE_DIR,
                "port": 8888,
            },
        )
        self.node.features.clear()

    def _remove_celery_lock(self):
        try:
            self.celery_lock.unlink()
        except FileNotFoundError:
            pass

    def test_horologia_hidden_without_celery_feature(self):
        resp = self.client.get(reverse("admin:index"))
        self.assertNotContains(resp, "5. Horologia</a>")

    def test_horologia_visible_with_celery_feature(self):
        feature = NodeFeature.objects.create(slug="celery-queue", display="Celery Queue")
        NodeFeatureAssignment.objects.create(node=self.node, feature=feature)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "5. Horologia</a>")

    def test_horologia_visible_with_celery_lock(self):
        self.celery_lock.write_text("")
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "5. Horologia</a>")

    @patch("pages.templatetags.admin_extras.recent_model_structure_changes")
    def test_dashboard_shows_recent_model_updates_above_recent_actions(self, recent_models):
        timestamp = timezone.now()
        recent_models.return_value = [
            {
                "label": "Widget",
                "app_label": "core",
                "applied": timestamp,
                "admin_url": "/admin/core/widget/",
            },
            {
                "label": "Gadget",
                "app_label": "core",
                "applied": timestamp - timedelta(minutes=5),
                "admin_url": "",
            },
        ]

        resp = self.client.get(reverse("admin:index"))

        self.assertContains(resp, gettext("Recently Updated Models"))
        self.assertContains(resp, "Widget")
        self.assertContains(resp, "Gadget")

        content = resp.content.decode()
        self.assertLess(
            content.index(gettext("Recently Updated Models")),
            content.index(gettext("Recent actions")),
        )

    def test_dashboard_shows_last_net_message(self):
        NetMessage.objects.all().delete()
        NetMessage.objects.create(subject="Older", body="First body")
        NetMessage.objects.create(subject="Latest", body="Signal ready")

        resp = self.client.get(reverse("admin:index"))

        self.assertContains(resp, gettext("Net message"))
        self.assertContains(resp, "Latest — Signal ready")
        self.assertNotContains(resp, gettext("No net messages available"))

    def test_dashboard_skips_blank_net_message(self):
        NetMessage.objects.all().delete()
        NetMessage.objects.create(subject="Filled", body="Reachable")
        NetMessage.objects.create(subject="", body="  ")

        resp = self.client.get(reverse("admin:index"))

        self.assertContains(resp, "Filled — Reachable")
        self.assertNotContains(resp, gettext("No net messages available"))

    def test_dashboard_shows_placeholder_without_net_message(self):
        NetMessage.objects.all().delete()

        resp = self.client.get(reverse("admin:index"))

        self.assertContains(resp, gettext("No net messages available"))

    def test_dashboard_shows_model_rules_success_message(self):
        charger = Charger.objects.create(
            charger_id="EVCS-100", last_heartbeat=timezone.now()
        )
        ChargerConfiguration.objects.create(charger_identifier="EVCS-100")
        CPFirmware.objects.create(source_charger=charger, payload_json={})

        resp = self.client.get(reverse("admin:index"))

        self.assertContains(resp, "model-rule-status--success")
        self.assertContains(resp, gettext("All rules met."))

    def test_dashboard_shows_model_rules_failure_message(self):
        healthy = Charger.objects.create(
            charger_id="EVCS-OK", last_heartbeat=timezone.now()
        )
        ChargerConfiguration.objects.create(charger_identifier="EVCS-OK")
        CPFirmware.objects.create(source_charger=healthy, payload_json={})
        Charger.objects.create(
            charger_id="EVCS-MISS",
            last_heartbeat=timezone.now() - timedelta(hours=2),
        )

        resp = self.client.get(reverse("admin:index"))

        self.assertContains(resp, "model-rule-status--error")
        self.assertContains(resp, "Missing CP Configuration for EVCS-MISS.")
        self.assertContains(resp, "Missing CP Firmware for EVCS-MISS.")
        self.assertContains(
            resp, "Missing EVCS heartbeat within the last hour for EVCS-MISS."
        )

    def test_dashboard_shows_evcs_heartbeat_failure_message(self):
        Charger.objects.create(
            charger_id="EVCS-LATE",
            last_heartbeat=timezone.now() - timedelta(hours=2),
        )

        resp = self.client.get(reverse("admin:index"))

        self.assertContains(resp, "model-rule-status--error")
        self.assertContains(
            resp, "Missing EVCS heartbeat within the last hour for EVCS-LATE."
        )


class AdminRunCommandTests(TransactionTestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="runner", password="pwd", email="runner@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(id=1, defaults={"name": "test", "domain": "testserver"})

    def _dummy_command(self, raise_error=False):
        class DummyCommand(BaseCommand):
            help = "Dummy command for tests"
            requires_system_checks = []

            def add_arguments(self, parser):
                parser.add_argument("--flag", action="store_true")

            def handle(self, *args, **options):
                if raise_error:
                    raise RuntimeError("boom")
                self.stdout.write("OK")
                if options.get("flag"):
                    self.stdout.write("FLAG")

        return DummyCommand()

    @patch("core.admin_commands.resolve_sigils")
    @patch("core.admin_commands.management.load_command_class")
    @patch("core.admin_commands.management.get_commands")
    def test_run_command_records_output(self, mock_get_commands, mock_load_command_class, mock_resolve_sigils):
        mock_resolve_sigils.side_effect = lambda value: "dummy --flag"
        mock_get_commands.return_value = {"dummy": "tests"}
        mock_load_command_class.return_value = self._dummy_command()

        response = self.client.post(reverse("admin:run_command"), {"command": "dummy"}, follow=True)

        self.assertEqual(response.status_code, 200)
        result = AdminCommandResult.objects.latest("created_at")
        self.assertEqual(result.command, "dummy")
        self.assertEqual(result.command_name, "dummy")
        self.assertEqual(result.resolved_command, "dummy --flag")
        self.assertTrue(result.success)
        self.assertIn("FLAG", result.stdout)
        self.assertContains(response, "FLAG")
        self.assertGreaterEqual(result.runtime.total_seconds(), 0)

    @patch("core.admin_commands.resolve_sigils")
    @patch("core.admin_commands.management.load_command_class")
    @patch("core.admin_commands.management.get_commands")
    def test_run_command_captures_traceback_on_error(
        self, mock_get_commands, mock_load_command_class, mock_resolve_sigils
    ):
        mock_resolve_sigils.return_value = "dummy"
        mock_get_commands.return_value = {"dummy": "tests"}
        mock_load_command_class.return_value = self._dummy_command(raise_error=True)

        response = self.client.post(reverse("admin:run_command"), {"command": "dummy"}, follow=True)

        self.assertEqual(response.status_code, 200)
        result = AdminCommandResult.objects.latest("created_at")
        self.assertFalse(result.success)
        self.assertIn("RuntimeError", result.traceback)
        self.assertContains(response, "RuntimeError")

    def test_history_paginates_results(self):
        for index in range(12):
            AdminCommandResult.objects.create(
                command=f"cmd{index}",
                resolved_command=f"cmd{index}",
                command_name=f"cmd{index}",
                stdout="",
                stderr="",
                traceback="",
                runtime=timedelta(seconds=index),
                exit_code=0 if index % 2 == 0 else 1,
                success=index % 2 == 0,
            )

        resp = self.client.get(reverse("admin:run_command"), {"page": 2})

        self.assertEqual(resp.status_code, 200)
        page_obj = resp.context["page_obj"]
        self.assertEqual(page_obj.number, 2)
        self.assertEqual(len(page_obj.object_list), 2)

    def test_run_command_requires_superuser(self):
        User = get_user_model()
        staff_user = User.objects.create_user(
            username="staff", password="pwd", email="staff@example.com", is_staff=True
        )

        self.client.force_login(staff_user)

        response = self.client.get(reverse("admin:run_command"))

        self.assertEqual(response.status_code, 403)


class AdminDashboardVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.staff_user = User.objects.create_user(
            username="viewer", password="pwd", email="viewer@example.com", is_staff=True
        )
        Site.objects.update_or_create(id=1, defaults={"name": "test", "domain": "testserver"})

    def test_run_command_button_hidden_for_non_superuser(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Run Command")


class AdminModelRuleTemplateTagTests(TestCase):
    def test_model_rule_status_uses_context_cache(self):
        Charger.objects.create(charger_id="EVCS-CACHE")
        ChargerConfiguration.objects.create(charger_identifier="EVCS-CACHE")

        context = Context({})

        with self.assertNumQueries(3):
            status = admin_extras.model_rule_status(
                context, "ocpp", "ChargerConfiguration"
            )
        self.assertTrue(status["success"])

        with self.assertNumQueries(0):
            cached = admin_extras.model_rule_status(
                context, "ocpp", "ChargerConfiguration"
            )

        self.assertEqual(cached, status)

    def test_model_rule_status_reports_evcs_heartbeat_success(self):
        Charger.objects.create(
            charger_id="EVCS-PASS", last_heartbeat=timezone.now()
        )

        context = Context({})

        status = admin_extras.model_rule_status(context, "ocpp", "Charger")

        self.assertTrue(status["success"])

    def test_model_rule_status_reports_evcs_heartbeat_failure(self):
        Charger.objects.create(
            charger_id="EVCS-FAIL", last_heartbeat=timezone.now() - timedelta(hours=3)
        )

        context = Context({})

        status = admin_extras.model_rule_status(context, "ocpp", "Charger")

        self.assertFalse(status["success"])
        self.assertIn("EVCS-FAIL", status["message"])
    def test_model_rule_status_for_nodes_requires_local_node(self):
        context = Context({})

        status = admin_extras.model_rule_status(context, "nodes", "Node")

        self.assertFalse(status["success"])
        self.assertIn("Local node record is missing.", status["message"])

    def test_model_rule_status_for_nodes_requires_upstream_node(self):
        mac = Node.get_current_mac()
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.create(
            hostname="local-node",
            mac_address=mac,
            public_endpoint="local-node",
            current_relation=Node.Relation.SELF,
            role=role,
        )

        context = Context({})
        status = admin_extras.model_rule_status(context, "nodes", "Node")

        self.assertFalse(status["success"])
        self.assertIn("At least one upstream node is required.", status["message"])

    def test_model_rule_status_for_watchtower_skips_upstream_requirement(self):
        mac = Node.get_current_mac()
        role, _ = NodeRole.objects.get_or_create(name="Watchtower")
        Node.objects.create(
            hostname="watchtower-node",
            mac_address=mac,
            public_endpoint="watchtower-node",
            current_relation=Node.Relation.SELF,
            role=role,
        )

        context = Context({})
        status = admin_extras.model_rule_status(context, "nodes", "Node")

        self.assertTrue(status["success"])
        self.assertEqual(status["message"], gettext("All rules met."))

    def test_model_rule_status_for_nodes_requires_recent_upstream_update(self):
        mac = Node.get_current_mac()
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.create(
            hostname="local-node",
            mac_address=mac,
            public_endpoint="local-node",
            current_relation=Node.Relation.SELF,
            role=role,
        )
        upstream = Node.objects.create(
            hostname="upstream-node",
            public_endpoint="upstream-node",
            current_relation=Node.Relation.UPSTREAM,
        )
        stale = timezone.now() - timedelta(days=2)
        Node.objects.filter(pk=upstream.pk).update(last_seen=stale)

        context = Context({})
        status = admin_extras.model_rule_status(context, "nodes", "Node")

        self.assertFalse(status["success"])
        self.assertIn(
            "No upstream nodes have checked in within the last 24 hours.",
            status["message"],
        )

    def test_model_rule_status_for_nodes_requires_local_role(self):
        mac = Node.get_current_mac()
        Node.objects.create(
            hostname="local-node",
            mac_address=mac,
            public_endpoint="local-node",
            current_relation=Node.Relation.SELF,
        )
        Node.objects.create(
            hostname="upstream-node",
            public_endpoint="upstream-node",
            current_relation=Node.Relation.UPSTREAM,
        )

        context = Context({})
        status = admin_extras.model_rule_status(context, "nodes", "Node")

        self.assertFalse(status["success"])
        self.assertIn("Local node is missing an assigned role.", status["message"])

    def test_model_rule_status_for_nodes_succeeds_when_all_checks_pass(self):
        mac = Node.get_current_mac()
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        local = Node.objects.create(
            hostname="local-node",
            mac_address=mac,
            public_endpoint="local-node",
            current_relation=Node.Relation.SELF,
            role=role,
        )
        upstream = Node.objects.create(
            hostname="upstream-node",
            public_endpoint="upstream-node",
            current_relation=Node.Relation.UPSTREAM,
        )
        Node.objects.filter(pk=upstream.pk).update(last_seen=timezone.now())

        context = Context({})
        status = admin_extras.model_rule_status(context, "nodes", "Node")

        self.assertTrue(status["success"])
        self.assertEqual(status["message"], gettext("All rules met."))


class RecentModelStructureChangesTests(TestCase):
    def _mock_migration_queryset(self, entries):
        migration_qs = Mock()
        migration_qs.order_by.return_value = migration_qs
        migration_qs.values_list.return_value = entries
        return migration_qs

    @patch("pages.templatetags.admin_extras.MigrationLoader")
    @patch("pages.templatetags.admin_extras.MigrationRecorder")
    def test_recent_model_structure_changes_ignore_data_only_migrations(
        self, recorder_cls, loader_cls
    ):
        applied_at = timezone.now()
        recorder = recorder_cls.return_value
        recorder.migration_qs = self._mock_migration_queryset(
            [
                ("core", "0002_create_widget", applied_at),
                ("core", "0003_data_update", applied_at - timedelta(minutes=1)),
            ]
        )

        create_model = migrations.CreateModel(
            name="Widget",
            fields=[("id", models.AutoField(primary_key=True))],
        )
        run_python = migrations.RunPython(migrations.RunPython.noop)

        loader = loader_cls.return_value
        loader.disk_migrations = {
            ("core", "0002_create_widget"): SimpleNamespace(operations=[create_model]),
            ("core", "0003_data_update"): SimpleNamespace(operations=[run_python]),
        }

        results = admin_extras.recent_model_structure_changes()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["label"], "Widget")
        self.assertEqual(results[0]["app_label"], "core")


class RelatedAdminModelsTagTests(TestCase):
    def test_related_admin_models_include_relationship_indicators(self):
        related = admin_extras.related_admin_models(CustomerAccount._meta)

        related_map = {entry["label"]: entry for entry in related}

        self.assertEqual(related_map["Users"]["relation_type"], "1:1")
        self.assertEqual(related_map["Energy Tariffs"]["relation_type"], "N:1")
        self.assertEqual(related_map["RFIDs"]["relation_type"], "N:N")


class AdminProtocolGroupingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="protocol_admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.superuser)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )
        from nodes.models import Node

        Node.objects.create(
            hostname="testserver",
            address="127.0.0.1",
            public_endpoint="test-forwarder",
        )

    def _build_request(self):
        request = self.factory.get("/admin/")
        request.user = self.superuser
        return request

    def test_cp_forwarder_lists_with_protocol_group(self):
        app_list = admin.site.get_app_list(self._build_request())
        ocpp_sections = [app for app in app_list if app["app_label"] == "ocpp"]
        self.assertTrue(ocpp_sections)
        ocpp_models = [model["object_name"] for model in ocpp_sections[0]["models"]]
        self.assertIn("CPForwarder", ocpp_models)
        self.assertFalse(any(app["app_label"] == "protocols" for app in app_list))

    def test_cp_forwarder_visible_in_ocpp_app_index(self):
        ocpp_list = admin.site.get_app_list(self._build_request(), app_label="ocpp")
        self.assertTrue(ocpp_list)
        ocpp_models = [model["object_name"] for model in ocpp_list[0]["models"]]
        self.assertIn("CPForwarder", ocpp_models)

    def test_cp_forwarder_row_includes_favorite_toggle(self):
        resp = self.client.get(reverse("admin:index"))
        content = resp.content.decode()
        match = re.search(r'id="protocols-cpforwarder">(?P<row>.*?)</th>', content, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertIn("favorite-star", match.group("row"))


class AdminSidebarTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="sidebar_admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )
        from nodes.models import Node

        Node.objects.create(hostname="testserver", address="127.0.0.1")

    def test_sidebar_app_groups_collapsible_script_present(self):
        url = reverse("admin:nodes_node_changelist")
        resp = self.client.get(url)
        self.assertContains(resp, 'id="admin-collapsible-apps"')


class AdminGoogleCalendarSidebarTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="calendar_admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )
        Node.objects.create(hostname="testserver", address="127.0.0.1")

    def test_calendar_module_hidden_without_profile(self):
        resp = self.client.get(reverse("admin:index"))
        self.assertNotContains(resp, 'id="google-calendar-module"', html=False)

    @patch("core.models.GoogleCalendarProfile.fetch_events")
    def test_calendar_module_shows_events_for_user(self, fetch_events):
        fetch_events.return_value = [
            {
                "summary": "Standup",
                "start": timezone.now(),
                "end": None,
                "all_day": False,
                "html_link": "https://calendar.google.com/event",
                "location": "HQ",
            }
        ]
        GoogleCalendarProfile.objects.create(
            user=self.admin,
            calendar_id="example@group.calendar.google.com",
            api_key="secret",
            display_name="Team Calendar",
        )

        resp = self.client.get(reverse("admin:index"))

        self.assertContains(resp, 'id="google-calendar-module"', html=False)
        self.assertContains(resp, "Standup")
        self.assertContains(resp, "Open full calendar")
        fetch_events.assert_called_once()

    @patch("core.models.GoogleCalendarProfile.fetch_events")
    def test_calendar_module_uses_group_profile(self, fetch_events):
        fetch_events.return_value = []
        group = SecurityGroup.objects.create(name="Calendar Group")
        self.admin.groups.add(group)
        GoogleCalendarProfile.objects.create(
            group=group,
            calendar_id="group@calendar.google.com",
            api_key="secret",
        )

        resp = self.client.get(reverse("admin:index"))

        self.assertContains(resp, 'id="google-calendar-module"', html=False)
        fetch_events.assert_called_once()


class ViewHistoryLoggingTests(TestCase):
    def setUp(self):
        self.client = Client()
        Site.objects.update_or_create(id=1, defaults={"name": "Terminal"})
        self.addCleanup(self._reset_purge_task)

    def _reset_purge_task(self):
        from django_celery_beat.models import PeriodicTask
        from core.celery_utils import periodic_task_name_variants

        PeriodicTask.objects.filter(
            name__in=periodic_task_name_variants("pages_purge_landing_leads")
        ).delete()

    def _create_local_node(self):
        node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": "127.0.0.1",
                "base_path": settings.BASE_DIR,
                "port": 8888,
            },
        )
        return node

    def _enable_celery_feature(self):
        node = self._create_local_node()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="celery-queue", defaults={"display": "Celery Queue"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        return node

    def test_successful_visit_creates_entry(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertEqual(resp.status_code, 200)
        entry = ViewHistory.objects.order_by("-visited_at").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.path, "/")
        self.assertEqual(entry.status_code, 200)
        self.assertEqual(entry.error_message, "")

    def test_error_visit_records_message(self):
        resp = self.client.get("/missing-page/")
        self.assertEqual(resp.status_code, 404)
        entry = (
            ViewHistory.objects.filter(path="/missing-page/")
            .order_by("-visited_at")
            .first()
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status_code, 404)
        self.assertNotEqual(entry.error_message, "")

    def test_debug_toolbar_requests_not_tracked(self):
        resp = self.client.get(reverse("pages:index"), {"djdt": "toolbar"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ViewHistory.objects.exists())

    def test_authenticated_user_last_visit_ip_updated(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="history_user", password="pwd", email="history@example.com"
        )
        self.assertTrue(self.client.login(username="history_user", password="pwd"))

        resp = self.client.get(
            reverse("pages:index"),
            HTTP_X_FORWARDED_FOR="203.0.113.5",
        )

        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.last_visit_ip_address, "203.0.113.5")

    def test_landing_visit_records_lead(self):
        self._enable_celery_feature()

        role = NodeRole.objects.create(name="landing-role")
        application = Application.objects.create(
            name="landing-tests-app", description=""
        )
        module = Module.objects.create(
            node_role=role,
            application=application,
            path="/",
            menu="Landing",
        )
        landing = module.landings.get(path="/")
        landing.label = "Home Landing"
        landing.track_leads = True
        landing.save(update_fields=["label", "track_leads"])

        resp = self.client.get(
            reverse("pages:index"), HTTP_REFERER="https://example.com/ref"
        )

        self.assertEqual(resp.status_code, 200)
        lead = LandingLead.objects.latest("created_on")
        self.assertEqual(lead.landing, landing)
        self.assertEqual(lead.path, "/")
        self.assertEqual(lead.referer, "https://example.com/ref")

    def test_pages_config_purges_old_view_history(self):
        ViewHistory.objects.all().delete()

        old_entry = ViewHistory.objects.create(
            path="/old/",
            method="GET",
            status_code=200,
            status_text="OK",
        )
        new_entry = ViewHistory.objects.create(
            path="/recent/",
            method="GET",
            status_code=200,
            status_text="OK",
        )

        ViewHistory.objects.filter(pk=old_entry.pk).update(
            visited_at=timezone.now() - timedelta(days=20)
        )
        ViewHistory.objects.filter(pk=new_entry.pk).update(
            visited_at=timezone.now() - timedelta(days=10)
        )

        config = django_apps.get_app_config("pages")
        config._purge_view_history()

        self.assertFalse(ViewHistory.objects.filter(pk=old_entry.pk).exists())
        self.assertTrue(ViewHistory.objects.filter(pk=new_entry.pk).exists())

    def test_landing_visit_does_not_record_lead_without_celery(self):
        role = NodeRole.objects.create(name="no-celery-role")
        application = Application.objects.create(
            name="no-celery-app", description=""
        )
        module = Module.objects.create(
            node_role=role,
            application=application,
            path="/",
            menu="Landing",
        )
        landing = module.landings.get(path="/")
        landing.label = "No Celery"
        landing.track_leads = True
        landing.save(update_fields=["label", "track_leads"])

        resp = self.client.get(reverse("pages:index"))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(LandingLead.objects.exists())

    def test_disabled_landing_does_not_record_lead(self):
        role = NodeRole.objects.create(name="landing-role-disabled")
        application = Application.objects.create(
            name="landing-disabled-app", description=""
        )
        module = Module.objects.create(
            node_role=role,
            application=application,
            path="/",
            menu="Landing",
        )
        landing = module.landings.get(path="/")
        landing.enabled = False
        landing.track_leads = True
        landing.save(update_fields=["enabled", "track_leads"])

        resp = self.client.get(reverse("pages:index"))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(LandingLead.objects.exists())


class ViewHistoryAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="history_admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )

    def _create_history(self, path: str, days_offset: int = 0, count: int = 1):
        for _ in range(count):
            entry = ViewHistory.objects.create(
                path=path,
                method="GET",
                status_code=200,
                status_text="OK",
                error_message="",
                view_name="pages:index",
            )
            if days_offset:
                entry.visited_at = timezone.now() - timedelta(days=days_offset)
                entry.save(update_fields=["visited_at"])

    def test_change_list_includes_graph_link(self):
        resp = self.client.get(reverse("admin:pages_viewhistory_changelist"))
        self.assertContains(resp, reverse("admin:pages_viewhistory_traffic_graph"))
        self.assertContains(resp, "Traffic graph")

    def test_graph_view_renders_canvas(self):
        resp = self.client.get(reverse("admin:pages_viewhistory_traffic_graph"))
        self.assertContains(resp, "viewhistory-chart")
        self.assertContains(resp, reverse("admin:pages_viewhistory_changelist"))
        self.assertContains(resp, static("core/vendor/chart.umd.min.js"))

    def test_graph_data_endpoint(self):
        ViewHistory.all_objects.all().delete()
        self._create_history("/", count=2)
        self._create_history("/about/", days_offset=1)
        url = reverse("admin:pages_viewhistory_traffic_data")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("labels", data)
        self.assertIn("datasets", data)
        self.assertGreater(len(data["labels"]), 0)
        totals = {
            dataset["label"]: sum(dataset["data"]) for dataset in data["datasets"]
        }
        self.assertEqual(totals.get("/"), 2)
        self.assertEqual(totals.get("/about/"), 1)

    def test_graph_data_endpoint_respects_days_parameter(self):
        ViewHistory.all_objects.all().delete()
        reference_date = date(2025, 5, 1)
        tz = timezone.get_current_timezone()
        path = "/range/"

        for offset in range(10):
            entry = ViewHistory.objects.create(
                path=path,
                method="GET",
                status_code=200,
                status_text="OK",
                error_message="",
                view_name="pages:index",
            )
            visited_date = reference_date - timedelta(days=offset)
            visited_at = timezone.make_aware(
                datetime.combine(visited_date, datetime_time(12, 0)), tz
            )
            entry.visited_at = visited_at
            entry.save(update_fields=["visited_at"])

        url = reverse("admin:pages_viewhistory_traffic_data")
        with patch("pages.admin.timezone.localdate", return_value=reference_date):
            resp = self.client.get(url, {"days": 7})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(len(data.get("labels", [])), 7)
        self.assertEqual(data.get("meta", {}).get("start"), (reference_date - timedelta(days=6)).isoformat())
        self.assertEqual(data.get("meta", {}).get("end"), reference_date.isoformat())

        totals = {
            dataset["label"]: sum(dataset["data"]) for dataset in data.get("datasets", [])
        }
        self.assertEqual(totals.get(path), 7)

    def test_graph_data_includes_late_evening_visits(self):
        target_date = date(2025, 9, 27)
        entry = ViewHistory.objects.create(
            path="/late/",
            method="GET",
            status_code=200,
            status_text="OK",
            error_message="",
            view_name="pages:index",
        )
        local_evening = datetime.combine(target_date, datetime_time(21, 30))
        aware_evening = timezone.make_aware(
            local_evening, timezone.get_current_timezone()
        )
        entry.visited_at = aware_evening.astimezone(datetime_timezone.utc)
        entry.save(update_fields=["visited_at"])

        url = reverse("admin:pages_viewhistory_traffic_data")
        with patch("pages.admin.timezone.localdate", return_value=target_date):
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        totals = {
            dataset["label"]: sum(dataset["data"]) for dataset in data["datasets"]
        }
        self.assertEqual(totals.get("/late/"), 1)

    def test_graph_data_filters_using_datetime_range(self):
        admin_view = ViewHistoryAdmin(ViewHistory, admin.site)
        with patch.object(ViewHistory.objects, "filter") as mock_filter:
            mock_queryset = mock_filter.return_value
            mock_queryset.exists.return_value = False
            admin_view._build_chart_data()

        kwargs = mock_filter.call_args.kwargs
        self.assertIn("visited_at__gte", kwargs)
        self.assertIn("visited_at__lt", kwargs)

    def test_admin_index_displays_widget(self):
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "viewhistory-mini-module")
        self.assertContains(resp, reverse("admin:pages_viewhistory_traffic_graph"))
        self.assertContains(resp, static("core/vendor/chart.umd.min.js"))


class AdminDashboardEmailWarningTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="email-admin", password="pwd", email="email@example.com"
        )
        self.client.force_login(self.superuser)

    def test_dashboard_shows_warning_when_email_disabled(self):
        with patch(
            "pages.templatetags.admin_extras.mailer.can_send_email",
            return_value=False,
        ):
            response = self.client.get(reverse("admin:index"))

        self.assertContains(response, "Email delivery is not configured.")
        self.assertContains(response, reverse("admin:teams_emailoutbox_add"))

    def test_dashboard_hides_warning_when_email_configured(self):
        with patch(
            "pages.templatetags.admin_extras.mailer.can_send_email",
            return_value=True,
        ):
            response = self.client.get(reverse("admin:index"))

        self.assertNotContains(response, "Email delivery is not configured.")


class LandingLeadAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="lead_admin", password="pwd", email="lead@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )
        self.node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": "127.0.0.1",
                "base_path": settings.BASE_DIR,
                "port": 8888,
            },
        )
        self.node.features.clear()
        self.addCleanup(self._reset_purge_task)

    def _reset_purge_task(self):
        from django_celery_beat.models import PeriodicTask
        from core.celery_utils import periodic_task_name_variants

        PeriodicTask.objects.filter(
            name__in=periodic_task_name_variants("pages_purge_landing_leads")
        ).delete()

    def test_changelist_warns_without_celery(self):
        url = reverse("admin:pages_landinglead_changelist")
        response = self.client.get(url)
        self.assertContains(
            response,
            "Landing leads are not being recorded because Celery is not running on this node.",
        )

    def test_changelist_no_warning_with_celery(self):
        feature, _ = NodeFeature.objects.get_or_create(
            slug="celery-queue", defaults={"display": "Celery Queue"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=self.node, feature=feature)
        url = reverse("admin:pages_landinglead_changelist")
        response = self.client.get(url)
        self.assertNotContains(
            response,
            "Landing leads are not being recorded because Celery is not running on this node.",
        )


class LandingLeadTaskTests(TestCase):
    def setUp(self):
        self.role = NodeRole.objects.create(name="lead-task-role")
        self.application = Application.objects.create(
            name="lead-task-app", description=""
        )
        self.module = Module.objects.create(
            node_role=self.role,
            application=self.application,
            path="/tasks",
            menu="Landing",
        )
        self.landing = Landing.objects.create(
            module=self.module,
            path="/tasks/",
            label="Tasks Landing",
            enabled=True,
        )

    def test_purge_expired_landing_leads_removes_old_records(self):
        from pages.tasks import purge_expired_landing_leads

        stale = LandingLead.objects.create(landing=self.landing, path="/tasks/")
        recent = LandingLead.objects.create(landing=self.landing, path="/tasks/")
        LandingLead.objects.filter(pk=stale.pk).update(
            created_on=timezone.now() - timedelta(days=31)
        )
        LandingLead.objects.filter(pk=recent.pk).update(
            created_on=timezone.now() - timedelta(days=5)
        )

        deleted = purge_expired_landing_leads()

        self.assertEqual(deleted, 1)
        self.assertFalse(LandingLead.objects.filter(pk=stale.pk).exists())
        self.assertTrue(LandingLead.objects.filter(pk=recent.pk).exists())


class LogViewerAdminTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.logs_dir = Path(settings.BASE_DIR) / "logs"
        self.logs_dir.mkdir(exist_ok=True)

    def tearDown(self):
        for path in list(self.logs_dir.iterdir()):
            if path.name == ".gitkeep":
                continue
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    def _create_log(self, name: str, content: str = "") -> Path:
        path = self.logs_dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def _build_request(self, params: dict | None = None):
        request = self.factory.get("/admin/logs/viewer/", params or {})

        class DummyUser:
            is_active = True
            is_staff = True
            is_superuser = True

            @property
            def is_authenticated(self):
                return True

            def has_perm(self, perm):
                return True

            def has_perms(self, perms):
                return True

            def has_module_perms(self, app_label):
                return True

            def get_username(self):
                return "tester"

        request.user = DummyUser()
        request.session = {}
        request.current_app = admin.site.name
        return request

    def _render(self, params: dict | None = None):
        request = self._build_request(params)
        context = {
            "site_title": "Constellation",
            "site_header": "Constellation",
            "site_url": "/",
            "available_apps": [],
        }
        with patch("pages.admin.admin.site.each_context", return_value=context), patch(
            "pages.context_processors.get_site", return_value=None
        ):
            response = log_viewer(request)
        return response

    def test_log_viewer_lists_available_logs(self):
        self._create_log("example.log", "example content")
        response = self._render()
        self.assertIn("example.log", response.context_data["available_logs"])

    def test_log_viewer_displays_selected_log(self):
        self._create_log("selected.log", "hello world")
        response = self._render({"log": "selected.log"})
        context = response.context_data
        self.assertEqual(context["selected_log"], "selected.log")
        self.assertIn("hello world", context["log_content"])

    def test_log_viewer_applies_line_limit(self):
        content = "\n".join(f"line {i}" for i in range(50))
        self._create_log("limited.log", content)
        response = self._render({"log": "limited.log", "limit": "20"})
        context = response.context_data
        self.assertEqual(context["log_limit_choice"], "20")
        self.assertIn("line 49", context["log_content"])
        self.assertIn("line 30", context["log_content"])
        self.assertNotIn("line 29", context["log_content"])

    def test_log_viewer_all_limit_returns_full_log(self):
        content = "first\nsecond\nthird"
        self._create_log("all.log", content)
        response = self._render({"log": "all.log", "limit": "all"})
        context = response.context_data
        self.assertEqual(context["log_limit_choice"], "all")
        self.assertIn("first", context["log_content"])
        self.assertIn("second", context["log_content"])

    def test_log_viewer_invalid_limit_defaults_to_20(self):
        content = "\n".join(f"item {i}" for i in range(5))
        self._create_log("invalid-limit.log", content)
        response = self._render({"log": "invalid-limit.log", "limit": "oops"})
        context = response.context_data
        self.assertEqual(context["log_limit_choice"], "20")

    def test_log_viewer_downloads_selected_log(self):
        self._create_log("download.log", "downloadable content")
        request = self._build_request({"log": "download.log", "download": "1"})
        context = {
            "site_title": "Constellation",
            "site_header": "Constellation",
            "site_url": "/",
            "available_apps": [],
        }
        with patch("pages.admin.admin.site.each_context", return_value=context), patch(
            "pages.context_processors.get_site", return_value=None
        ):
            response = log_viewer(request)
        self.assertIsInstance(response, FileResponse)
        self.assertIn("attachment", response["Content-Disposition"])
        content = b"".join(response.streaming_content).decode()
        self.assertIn("downloadable content", content)

    def test_log_viewer_reports_missing_log(self):
        response = self._render({"log": "missing.log"})
        self.assertIn("requested log could not be found", response.context_data["log_error"])

    def test_log_viewer_ignores_nested_files(self):
        nested = self.logs_dir / "nested"
        nested.mkdir(exist_ok=True)
        (nested / "hidden.log").write_text("hidden", encoding="utf-8")
        self._create_log("root.log", "root")
        response = self._render()
        self.assertIn("root.log", response.context_data["available_logs"])
        self.assertNotIn("hidden.log", response.context_data["available_logs"])

    def test_log_viewer_ignores_hidden_files(self):
        hidden_log = self.logs_dir / ".hidden.log"
        hidden_log.write_text("secret", encoding="utf-8")
        self._create_log("visible.log", "visible")
        response = self._render()
        self.assertIn("visible.log", response.context_data["available_logs"])
        self.assertNotIn(".hidden.log", response.context_data["available_logs"])

class AdminModelStatusTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="status_admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )
        from nodes.models import Node

        Node.objects.create(hostname="testserver", address="127.0.0.1")

    def test_status_indicator_removed(self):
        resp = self.client.get(reverse("admin:index"))
        self.assertNotContains(resp, "class=\"model-status")

        changelist = self.client.get(reverse("admin:pages_application_changelist"))
        self.assertNotContains(changelist, "class=\"model-status")


class _FakeQuerySet(list):
    def only(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self


class SiteConfigurationStagingTests(SimpleTestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.config_path = Path(self.tmpdir) / "nginx-sites.json"
        self._path_patcher = patch(
            "pages.site_config._sites_config_path", side_effect=lambda: self.config_path
        )
        self._path_patcher.start()
        self.addCleanup(self._path_patcher.stop)
        self._model_patcher = patch("pages.site_config.apps.get_model")
        self.mock_get_model = self._model_patcher.start()
        self.addCleanup(self._model_patcher.stop)

    def _read_config(self):
        if not self.config_path.exists():
            return None
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _set_sites(self, sites):
        queryset = _FakeQuerySet(sites)

        class _Manager:
            @staticmethod
            def filter(**kwargs):
                return queryset

        self.mock_get_model.return_value = SimpleNamespace(objects=_Manager())

    def test_managed_site_persists_configuration(self):
        self._set_sites([SimpleNamespace(domain="example.com", require_https=True)])
        site_config.update_local_nginx_scripts()
        config = self._read_config()
        self.assertEqual(
            config,
            [
                {
                    "domain": "example.com",
                    "require_https": True,
                }
            ],
        )

    def test_disabling_managed_site_removes_entry(self):
        primary = SimpleNamespace(domain="primary.test", require_https=False)
        secondary = SimpleNamespace(domain="secondary.test", require_https=False)
        self._set_sites([primary, secondary])
        site_config.update_local_nginx_scripts()
        config = self._read_config()
        self.assertEqual(
            [entry["domain"] for entry in config],
            ["primary.test", "secondary.test"],
        )

        self._set_sites([secondary])
        site_config.update_local_nginx_scripts()
        config = self._read_config()
        self.assertEqual(config, [{"domain": "secondary.test", "require_https": False}])

        self._set_sites([])
        site_config.update_local_nginx_scripts()
        self.assertIsNone(self._read_config())

    def test_require_https_toggle_updates_configuration(self):
        site = SimpleNamespace(domain="secure.example", require_https=False)
        self._set_sites([site])
        site_config.update_local_nginx_scripts()
        config = self._read_config()
        self.assertEqual(config, [{"domain": "secure.example", "require_https": False}])

        site.require_https = True
        self._set_sites([site])
        site_config.update_local_nginx_scripts()
        config = self._read_config()
        self.assertEqual(config, [{"domain": "secure.example", "require_https": True}])


class SiteRequireHttpsMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = SiteHttpsRedirectMiddleware(lambda request: HttpResponse("ok"))
        self.secure_site = SimpleNamespace(domain="secure.test", require_https=True)

    def test_http_request_redirects_to_https(self):
        request = self.factory.get("/", HTTP_HOST="secure.test")
        request.site = self.secure_site
        response = self.middleware(request)
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response["Location"].startswith("https://secure.test"))

    def test_secure_request_not_redirected(self):
        request = self.factory.get("/", HTTP_HOST="secure.test", secure=True)
        request.site = self.secure_site
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_forwarded_proto_respected(self):
        request = self.factory.get(
            "/", HTTP_HOST="secure.test", HTTP_X_FORWARDED_PROTO="https"
        )
        request.site = self.secure_site
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

        self.secure_site.require_https = False
        request = self.factory.get("/", HTTP_HOST="secure.test")
        request.site = self.secure_site
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)


class SiteAdminRegisterCurrentTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="site-admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "Constellation", "domain": "arthexis.com"}
        )

    def test_register_current_creates_site(self):
        resp = self.client.get(reverse("admin:pages_siteproxy_changelist"))
        self.assertContains(resp, "Register Current")

        resp = self.client.get(reverse("admin:pages_siteproxy_register_current"))
        self.assertRedirects(resp, reverse("admin:pages_siteproxy_changelist"))
        self.assertTrue(Site.objects.filter(domain="testserver").exists())
        site = Site.objects.get(domain="testserver")
        self.assertEqual(site.name, "testserver")

    @override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"])
    def test_register_current_ip_sets_pages_name(self):
        resp = self.client.get(
            reverse("admin:pages_siteproxy_register_current"), HTTP_HOST="127.0.0.1"
        )
        self.assertRedirects(resp, reverse("admin:pages_siteproxy_changelist"))
        site = Site.objects.get(domain="127.0.0.1")
        self.assertEqual(site.name, "")


class SiteAdminPermissionFallbackTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="site-staff",
            password="pwd",
            email="staff@example.com",
            is_staff=True,
        )
        self.site_admin = admin.site._registry[SiteProxy]
        self.view_permission = Permission.objects.get(
            codename="view_site",
            content_type__app_label="sites",
            content_type__model="site",
        )
        self.change_permission = Permission.objects.get(
            codename="change_site",
            content_type__app_label="sites",
            content_type__model="site",
        )

    def _build_request(self):
        request = self.factory.get("/admin/pages/siteproxy/")
        request.user = self.staff
        return request

    def test_has_view_permission_allows_sites_permissions(self):
        request = self._build_request()
        self.assertFalse(self.site_admin.has_view_permission(request))

        self.staff.user_permissions.add(self.view_permission)
        User = get_user_model()
        self.staff = User.objects.get(pk=self.staff.pk)
        self.assertTrue(self.staff.has_perm("sites.view_site"))
        request = self._build_request()
        self.assertTrue(self.site_admin.has_view_permission(request))
        self.assertTrue(self.site_admin.has_module_permission(request))

    def test_has_change_permission_allows_sites_permissions(self):
        request = self._build_request()
        self.assertFalse(self.site_admin.has_change_permission(request))

        self.staff.user_permissions.add(self.change_permission)
        User = get_user_model()
        self.staff = User.objects.get(pk=self.staff.pk)
        self.assertTrue(self.staff.has_perm("sites.change_site"))
        request = self._build_request()
        self.assertTrue(self.site_admin.has_change_permission(request))


@pytest.mark.feature("screenshot-poll")
class SiteAdminScreenshotTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="screenshot-admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "Terminal", "domain": "testserver"}
        )
        self.node = Node.objects.create(
            hostname="localhost",
            address="127.0.0.1",
            port=80,
            mac_address=Node.get_current_mac(),
        )

    @patch("pages.admin.capture_screenshot")
    def test_capture_screenshot_action(self, mock_capture):
        screenshot_dir = settings.LOG_DIR / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        file_path = screenshot_dir / "test.png"
        file_path.write_bytes(b"frontpage")
        mock_capture.return_value = Path("screenshots/test.png")
        url = reverse("admin:pages_siteproxy_changelist")
        response = self.client.post(
            url,
            {"action": "capture_screenshot", "_selected_action": [1]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1
        )
        screenshot = ContentSample.objects.filter(kind=ContentSample.IMAGE).first()
        self.assertEqual(screenshot.node, self.node)
        self.assertEqual(screenshot.path, "screenshots/test.png")
        self.assertEqual(screenshot.method, "ADMIN")
        link = reverse("admin:nodes_contentsample_change", args=[screenshot.pk])
        self.assertContains(response, link)
        mock_capture.assert_called_once_with("http://testserver/")


class SiteAdminReloadFixturesTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="fixture-admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "Terminal", "domain": "testserver"}
        )

    @patch("pages.admin.call_command")
    def test_reload_site_fixtures_action(self, mock_call_command):
        response = self.client.post(
            reverse("admin:pages_siteproxy_changelist"),
            {"action": "reload_site_fixtures", "_selected_action": [1]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        fixtures_dir = Path(settings.BASE_DIR) / "core" / "fixtures"
        expected = sorted(fixtures_dir.glob("references__00_site_*.json"))
        sigil_fixture = fixtures_dir / "sigil_roots__site.json"
        if sigil_fixture.exists():
            expected.append(sigil_fixture)

        expected_calls = [
            call("loaddata", str(path), verbosity=0) for path in expected
        ]
        self.assertEqual(mock_call_command.call_args_list, expected_calls)

        if expected_calls:
            self.assertContains(
                response,
                f"Reloaded {len(expected_calls)} site fixtures.",
            )


class AdminBadgesWebsiteTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="badge-admin2", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "localhost", "address": "127.0.0.1", "role": role},
        )
        Site.objects.update_or_create(
            id=1, defaults={"name": "", "domain": "127.0.0.1"}
        )

    @override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"])
    def test_badge_shows_domain_when_site_name_blank(self):
        resp = self.client.get(reverse("admin:index"), HTTP_HOST="127.0.0.1")
        self.assertContains(resp, "SITE: 127.0.0.1")


class NavAppsTests(TestCase):
    def setUp(self):
        self.client = Client()
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "localhost", "address": "127.0.0.1", "role": role},
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "127.0.0.1", "name": ""}
        )
        app = Application.objects.create(name="Readme")
        Module.objects.create(
            node_role=role, application=app, path="/", is_default=True, menu="Cookbooks"
        )

    def test_nav_pill_renders(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, "COOKBOOKS")
        self.assertContains(resp, "badge rounded-pill")

    def test_nav_pill_renders_with_port(self):
        resp = self.client.get(reverse("pages:index"), HTTP_HOST="127.0.0.1:8888")
        self.assertContains(resp, "COOKBOOKS")

    def test_nav_pill_uses_menu_field(self):
        site_app = Module.objects.get()
        site_app.menu = "Docs"
        site_app.save()
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'badge rounded-pill text-bg-secondary">DOCS')
        self.assertNotContains(resp, 'badge rounded-pill text-bg-secondary">COOKBOOKS')

    def test_app_without_root_url_excluded(self):
        role = NodeRole.objects.get(name="Terminal")
        app = Application.objects.create(name="core")
        Module.objects.create(node_role=role, application=app, path="/core/")
        resp = self.client.get(reverse("pages:index"))
        self.assertNotContains(resp, 'href="/core/"')


class RoleLandingRedirectTests(TestCase):
    def setUp(self):
        self.client = Client()
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        self.node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "localhost", "address": "127.0.0.1"},
        )
        self.ocpp_app, _ = Application.objects.get_or_create(name="ocpp")
        self.user_model = get_user_model()

    def _ensure_landing(
        self, role: NodeRole, landing_path: str, label: str
    ) -> Landing:
        module, _ = Module.objects.get_or_create(
            node_role=role,
            application=self.ocpp_app,
            defaults={"path": "/ocpp/", "menu": "Charge Points"},
        )
        if module.path != "/ocpp/":
            module.path = "/ocpp/"
            module.save(update_fields=["path"])
        landing, _ = Landing.objects.get_or_create(
            module=module,
            path=landing_path,
            defaults={
                "label": label,
                "enabled": True,
                "description": "",
            },
        )
        if landing.label != label or not landing.enabled or landing.description:
            landing.label = label
            landing.enabled = True
            landing.description = ""
            landing.save(update_fields=["label", "enabled", "description"])
        return landing

    def _configure_role_landing(
        self, role_name: str, landing_path: str, label: str, priority: int = 0
    ) -> str:
        role, _ = NodeRole.objects.get_or_create(name=role_name)
        self.node.role = role
        self.node.save(update_fields=["role"])
        landing = self._ensure_landing(role, landing_path, label)
        RoleLanding.objects.update_or_create(
            node_role=role,
            defaults={"landing": landing, "is_deleted": False, "priority": priority},
        )
        return landing_path

    def test_satellite_redirects_to_dashboard(self):
        target = self._configure_role_landing(
            "Satellite", "/ocpp/cpms/dashboard/", "CPMS Online Dashboard"
        )
        resp = self.client.get(reverse("pages:index"))
        self.assertRedirects(resp, target, fetch_redirect_response=False)

    def test_control_redirects_to_rfid(self):
        target = self._configure_role_landing(
            "Control", "/ocpp/rfid/validator/", "Identity Validator"
        )
        resp = self.client.get(reverse("pages:index"))
        self.assertRedirects(resp, target, fetch_redirect_response=False)

    def test_security_group_redirect_takes_priority(self):
        self._configure_role_landing(
            "Control", "/ocpp/rfid/validator/", "Identity Validator"
        )
        role = self.node.role
        group = SecurityGroup.objects.create(name="Operators")
        group_landing = self._ensure_landing(role, "/ocpp/group/", "Group Landing")
        RoleLanding.objects.update_or_create(
            security_group=group,
            defaults={"landing": group_landing, "priority": 5, "is_deleted": False},
        )
        user = self.user_model.objects.create_user("group-user")
        user.groups.add(group)
        self.client.force_login(user)
        resp = self.client.get(reverse("pages:index"))
        self.assertRedirects(
            resp, group_landing.path, fetch_redirect_response=False
        )

    def test_user_redirect_overrides_group_with_higher_priority(self):
        self._configure_role_landing(
            "Control", "/ocpp/rfid/validator/", "Identity Validator"
        )
        role = self.node.role
        group = SecurityGroup.objects.create(name="Operators")
        group_landing = self._ensure_landing(role, "/ocpp/group/", "Group Landing")
        RoleLanding.objects.update_or_create(
            security_group=group,
            defaults={"landing": group_landing, "priority": 3, "is_deleted": False},
        )
        user = self.user_model.objects.create_user("priority-user")
        user.groups.add(group)
        user_landing = self._ensure_landing(role, "/ocpp/user/", "User Landing")
        RoleLanding.objects.update_or_create(
            user=user,
            defaults={"landing": user_landing, "priority": 10, "is_deleted": False},
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("pages:index"))
        self.assertRedirects(
            resp, user_landing.path, fetch_redirect_response=False
        )


class WatchtowerNavTests(TestCase):
    def setUp(self):
        self.client = Client()
        role, _ = NodeRole.objects.get_or_create(name="Watchtower")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": role,
            },
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "arthexis.com", "name": "Arthexis"}
        )
        fixtures = [
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "default__application_pages.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "watchtower__application_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "watchtower__module_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "watchtower__landing_ocpp_dashboard.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "watchtower__landing_ocpp_cp_simulator.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "watchtower__landing_ocpp_maintenance_request.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "watchtower__landing_ocpp_rfid.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "watchtower__module_readme.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "watchtower__landing_readme.json",
            ),
        ]
        call_command("loaddata", *map(str, fixtures))
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rfid-scanner", defaults={"display": "RFID Scanner"}
        )
        node = Node.get_local()
        if node:
            NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)

    def test_rfid_pill_hidden(self):
        resp = self.client.get(reverse("pages:index"))
        nav_labels = [
            module.menu_label.upper() for module in resp.context["nav_modules"]
        ]
        self.assertNotIn("RFID", nav_labels)
        self.assertTrue(
            Module.objects.filter(
                path="/ocpp/", node_role__name="Watchtower"
            ).exists()
        )
        self.assertFalse(
            Module.objects.filter(
                path="/ocpp/rfid/",
                node_role__name="Watchtower",
                is_deleted=False,
            ).exists()
        )
        ocpp_module = next(
            module
            for module in resp.context["nav_modules"]
            if module.menu_label.upper() == "CHARGERS"
        )
        landing_labels = [landing.label for landing in ocpp_module.enabled_landings]
        self.assertIn("Identity Validator", landing_labels)

    @override_settings(ALLOWED_HOSTS=["testserver", "arthexis.com"])
    def test_cookbooks_pill_visible_for_arthexis(self):
        resp = self.client.get(
            reverse("pages:index"), HTTP_HOST="arthexis.com"
        )
        self.assertContains(resp, 'badge rounded-pill text-bg-secondary">COOKBOOKS')

    def test_ocpp_dashboard_visible(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'href="/ocpp/cpms/dashboard/"')


class NavPriorityOrderingTests(TestCase):
    def setUp(self):
        self.client = Client()
        role, _ = NodeRole.objects.get_or_create(name="Watchtower")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": role,
            },
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "Arthexis"}
        )

        self.role = role
        self.applications = {
            name: Application.objects.get_or_create(name=name)[0]
            for name in ("pages", "ocpp", "awg", "constellation")
        }

    def _add_module(
        self, app_name: str, path: str, menu: str, priority: int, label: str
    ) -> Module:
        module = Module.objects.create(
            node_role=self.role,
            application=self.applications[app_name],
            path=path,
            menu=menu,
            priority=priority,
        )
        Landing.objects.create(module=module, path="/", label=label)
        return module

    def test_public_nav_orders_by_priority(self):
        self._add_module("pages", "/read/", "Cookbooks", 1, "Cookbooks")
        self._add_module("ocpp", "/ocpp/", "Charge Points", 2, "Charge Points")
        self._add_module("awg", "/awg/", "", 3, "Calculators")
        self._add_module(
            "constellation",
            "/constellation/",
            "Constellation",
            4,
            "Constellation",
        )

        response = self.client.get(reverse("pages:index"))
        nav_labels = [module.menu_label for module in response.context["nav_modules"]]

        self.assertEqual(
            nav_labels,
            ["Cookbooks", "Charge Points", "Calculators", "Constellation"],
        )


class ReleaseModuleNavTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user_model = get_user_model()
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": role,
            },
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "Terminal"}
        )
        fixtures = [
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "default__application_awg.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "localhost__application_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "localhost__module_awg.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "localhost__module_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "localhost__landing_ocpp_dashboard.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "localhost__landing_ocpp_cp_simulator.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "localhost__landing_ocpp_maintenance_request.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "localhost__landing_ocpp_rfid.json",
            ),
        ]

        for fixture_path in fixtures:
            call_command("loaddata", str(fixture_path))

        self.release_group, _ = SecurityGroup.objects.get_or_create(
            name="Release Managers"
        )
        self.release_module_fixture = Path(
            settings.BASE_DIR, "pages", "fixtures", "localhost__module_release.json"
        )
        self.release_landing_fixture = Path(
            settings.BASE_DIR, "pages", "fixtures", "localhost__landing_release.json"
        )

    def test_release_fixtures_are_absent(self):
        self.assertFalse(self.release_module_fixture.exists())
        self.assertFalse(self.release_landing_fixture.exists())
        self.assertFalse(Module.objects.filter(path="/release/").exists())
        self.assertFalse(Landing.objects.filter(path="/release/").exists())

    def test_release_module_hidden_for_anonymous(self):
        response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, 'badge rounded-pill text-bg-secondary">RELEASE')
        nav_labels = [
            module.menu_label.upper() for module in response.context["nav_modules"]
        ]
        self.assertNotIn("RELEASE", nav_labels)

    def test_release_module_hidden_for_release_manager(self):
        user = self.user_model.objects.create_user(
            "release-admin", password="test", is_staff=True
        )
        user.groups.add(self.release_group)
        self.client.force_login(user)
        response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, 'badge rounded-pill text-bg-secondary">RELEASE')
        nav_labels = [
            module.menu_label.upper() for module in response.context["nav_modules"]
        ]
        self.assertNotIn("RELEASE", nav_labels)

    def test_release_module_hidden_for_non_member_staff(self):
        user = self.user_model.objects.create_user(
            "staff-user", password="test", is_staff=True
        )
        self.client.force_login(user)
        response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, 'badge rounded-pill text-bg-secondary">RELEASE')
        nav_labels = [
            module.menu_label.upper() for module in response.context["nav_modules"]
        ]
        self.assertNotIn("RELEASE", nav_labels)


class ControlNavTests(TestCase):
    def setUp(self):
        self.client = Client()
        role, _ = NodeRole.objects.get_or_create(name="Control")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": role,
            },
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        fixtures = [
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "default__application_pages.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "control__application_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "control__module_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "control__landing_ocpp_dashboard.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "control__landing_ocpp_cp_simulator.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "control__landing_ocpp_maintenance_request.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "control__landing_ocpp_rfid.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "control__module_readme.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "control__landing_readme.json",
            ),
        ]
        call_command("loaddata", *map(str, fixtures))

    def _write_doc(self, relative_path: str, content: str) -> Path:
        file_path = Path(settings.BASE_DIR, relative_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        self.addCleanup(lambda: file_path.unlink(missing_ok=True))
        return file_path

    def test_ocpp_dashboard_visible(self):
        user = get_user_model().objects.create_user("control", password="pw")
        self.client.force_login(user)
        resp = self.client.get(reverse("pages:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'href="/ocpp/cpms/dashboard/"')
        self.assertContains(
            resp, 'badge rounded-pill text-bg-secondary">CHARGERS'
        )

    def test_header_links_visible_when_defined(self):
        Reference.objects.create(
            alt_text="Console",
            value="https://example.com/console",
            show_in_header=True,
        )

        resp = self.client.get(reverse("pages:index"))

        self.assertIn("header_references", resp.context)
        self.assertTrue(resp.context["header_references"])
        self.assertContains(resp, "CONSTELLATION")
        self.assertContains(resp, 'href="https://example.com/console"')

    def test_header_links_hidden_when_flag_false(self):
        Reference.objects.create(
            alt_text="Hidden",
            value="https://example.com/hidden",
            show_in_header=False,
        )

        resp = self.client.get(reverse("pages:index"))

        self.assertIn("header_references", resp.context)
        self.assertFalse(resp.context["header_references"])
        self.assertNotContains(resp, "https://example.com/hidden")

    def test_header_link_hidden_when_only_site_matches(self):
        terminal_role, _ = NodeRole.objects.get_or_create(name="Terminal")
        site = Site.objects.get(domain="testserver")
        reference = Reference.objects.create(
            alt_text="Restricted",
            value="https://example.com/restricted",
            show_in_header=True,
        )
        reference.roles.add(terminal_role)
        reference.sites.add(site)

        resp = self.client.get(reverse("pages:index"))

        self.assertIn("header_references", resp.context)
        self.assertFalse(resp.context["header_references"])
        self.assertNotContains(resp, "https://example.com/restricted")

    def test_header_links_hidden_when_validation_fails(self):
        Reference.objects.create(
            alt_text="Broken",
            value="https://example.com/broken",
            show_in_header=True,
            validation_status=500,
            validated_url_at=timezone.now(),
        )

        resp = self.client.get(reverse("pages:index"))

        self.assertIn("header_references", resp.context)
        self.assertFalse(resp.context["header_references"])
        self.assertNotContains(resp, "CONSTELLATION")

    def test_readme_pill_visible(self):
        resp = self.client.get(reverse("pages:readme"))
        self.assertContains(resp, 'href="/read/docs/cookbooks/install-start-stop-upgrade-uninstall"')
        self.assertContains(resp, 'badge rounded-pill text-bg-secondary">COOKBOOKS')

    def test_cookbook_pill_has_no_dropdown(self):
        module = Module.objects.get(node_role__name="Control", path="/read/")
        Landing.objects.create(
            module=module,
            path="/man/",
            label="Manuals",
            enabled=True,
        )

        resp = self.client.get(reverse("pages:readme"))

        self.assertContains(
            resp,
            '<a class="nav-link" href="/read/docs/cookbooks/install-start-stop-upgrade-uninstall"><span class="badge rounded-pill text-bg-secondary">COOKBOOKS</span></a>',
            html=True,
        )
        self.assertNotContains(resp, 'dropdown-item" href="/man/"')

    def test_readme_page_includes_qr_share(self):
        resp = self.client.get(reverse("pages:readme"), {"section": "intro"})
        self.assertContains(resp, 'id="reader-qr"')
        self.assertContains(
            resp,
            'data-url="http://testserver/read/?section=intro"',
        )
        self.assertNotContains(resp, "Scan this page")
        self.assertNotContains(
            resp, 'class="small text-break text-muted mt-3 mb-0"'
        )

    def test_readme_document_by_name(self):
        resp = self.client.get(reverse("pages:readme-document", args=["AGENTS.md"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Agent Guidelines")

    def test_readme_document_by_relative_path(self):
        resp = self.client.get(
            reverse(
                "pages:readme-document",
                args=["docs/development/maintenance-roadmap.md"],
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Maintenance Improvement Proposals")

    def test_readme_document_rejects_traversal(self):
        resp = self.client.get("/read/../../SECRET.md")
        self.assertEqual(resp.status_code, 404)

    def test_readme_plain_text_document(self):
        self._write_doc("docs/sample-viewer.txt", "Plain text file\nSecond line")
        resp = self.client.get(
            reverse("pages:readme-document", args=["docs/sample-viewer.txt"])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="reader-plain-text', html=False)
        self.assertContains(resp, "Plain text file")
        self.assertContains(resp, "Second line")
        self.assertFalse(resp.context["toc"])

    def test_readme_csv_document_renders_table(self):
        self._write_doc(
            "docs/sample-data.csv",
            "name,score\nAlice,10\nBob,20",
        )
        resp = self.client.get(
            reverse("pages:readme-document", args=["docs/sample-data.csv"])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp,
            'class="table table-striped table-bordered table-sm reader-table"',
            html=False,
        )
        self.assertContains(resp, "<th scope=\"col\">name</th>", html=True)
        self.assertContains(resp, "<td>Alice</td>", html=True)
        self.assertFalse(resp.context["toc"])

    def test_readme_unknown_document_uses_code_viewer(self):
        self._write_doc(
            "docs/sample-script.sh",
            "#!/bin/bash\necho Script ready",
        )
        resp = self.client.get(
            reverse("pages:readme-document", args=["docs/sample-script.sh"])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="reader-code-viewer', html=False)
        self.assertContains(resp, "echo Script ready")
        self.assertFalse(resp.context["toc"])

    def test_docs_route_redirects_to_reader(self):
        resp = self.client.get("/docs/cookbooks/sigils.md")
        expected = reverse(
            "pages:readme-document", args=["docs/cookbooks/sigils.md"]
        )
        self.assertRedirects(resp, expected, fetch_redirect_response=False)

    def test_docs_root_redirects_to_readme(self):
        resp = self.client.get("/docs/")
        expected = reverse("pages:readme")
        self.assertRedirects(resp, expected, fetch_redirect_response=False)


class SatelliteNavTests(TestCase):
    def setUp(self):
        self.client = Client()
        role, _ = NodeRole.objects.get_or_create(name="Satellite")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": role,
            },
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        fixtures = [
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "default__application_pages.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "satellite_box__application_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "satellite_box__module_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "satellite_box__landing_ocpp_dashboard.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "satellite_box__landing_ocpp_cp_simulator.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "satellite_box__landing_ocpp_maintenance_request.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "satellite_box__landing_ocpp_rfid.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "satellite_box__module_readme.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "satellite_box__landing_readme.json",
            ),
        ]
        call_command("loaddata", *map(str, fixtures))

    def test_readme_pill_visible(self):
        resp = self.client.get(reverse("pages:readme"))
        self.assertContains(resp, 'href="/read/docs/cookbooks/install-start-stop-upgrade-uninstall"')
        self.assertContains(resp, 'badge rounded-pill text-bg-secondary">COOKBOOKS')


class PowerNavTests(TestCase):
    def setUp(self):
        self.client = Client()
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "localhost", "address": "127.0.0.1", "role": role},
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        awg_app, _ = Application.objects.get_or_create(name="awg")
        awg_module, _ = Module.objects.get_or_create(
            node_role=role, application=awg_app, path="/awg/"
        )
        awg_module.create_landings()
        manuals_app, _ = Application.objects.get_or_create(name="pages")
        man_module, _ = Module.objects.get_or_create(
            node_role=role, application=manuals_app, path="/man/"
        )
        man_module.create_landings()
        User = get_user_model()
        self.user = User.objects.create_user("user", password="pw")

    def test_power_pill_lists_calculators(self):
        resp = self.client.get(reverse("pages:index"))
        power_module = None
        for module in resp.context["nav_modules"]:
            if module.path == "/awg/":
                power_module = module
                break
        self.assertIsNotNone(power_module)
        self.assertEqual(power_module.menu_label.upper(), "CALCULATORS")
        landing_labels = {landing.label for landing in power_module.enabled_landings}
        self.assertIn("AWG Cable Calculator", landing_labels)
        self.assertIn("Future Event Calculator", landing_labels)

    def test_manual_pill_label(self):
        resp = self.client.get(reverse("pages:index"))
        manuals_module = None
        for module in resp.context["nav_modules"]:
            if module.path == "/man/":
                manuals_module = module
                break
        self.assertIsNotNone(manuals_module)
        self.assertEqual(manuals_module.menu_label.upper(), "MANUAL")
        landing_labels = {landing.label for landing in manuals_module.enabled_landings}
        self.assertIn("Manuals", landing_labels)

    def test_energy_tariff_visible_when_logged_in(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("pages:index"))
        power_module = None
        for module in resp.context["nav_modules"]:
            if module.path == "/awg/":
                power_module = module
                break
        self.assertIsNotNone(power_module)
        landing_labels = {landing.label for landing in power_module.enabled_landings}
        self.assertIn("AWG Cable Calculator", landing_labels)
        self.assertIn("Future Event Calculator", landing_labels)
        self.assertIn("Energy Tariff Calculator", landing_labels)

    def test_locked_landing_shows_lock_icon(self):
        resp = self.client.get(reverse("pages:index"))
        html = resp.content.decode()
        energy_index = html.find("Energy Tariff Calculator")
        self.assertGreaterEqual(energy_index, 0)
        icon_index = html.find("dropdown-lock-icon", energy_index, energy_index + 300)
        self.assertGreaterEqual(icon_index, 0)

    def test_lock_icon_disappears_after_login(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("pages:index"))
        html = resp.content.decode()
        energy_index = html.find("Energy Tariff Calculator")
        self.assertGreaterEqual(energy_index, 0)
        icon_index = html.find("dropdown-lock-icon", energy_index, energy_index + 300)
        self.assertEqual(icon_index, -1)

    def test_calculator_landings_deduplicate_trailing_slashes(self):
        role = NodeRole.objects.get(name="Terminal")
        module = Module.objects.get(node_role=role, path="/awg/")
        Landing.objects.create(module=module, path="/awg", label="AWG Cable Calculator")

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.session = self.client.session

        context = nav_links(request)

        power_module = None
        for module in context["nav_modules"]:
            if module.path == "/awg/":
                power_module = module
                break

        self.assertIsNotNone(power_module)

        normalized_paths = [
            landing.path.rstrip("/") or "/" for landing in power_module.enabled_landings
        ]
        self.assertEqual(len(normalized_paths), len(set(normalized_paths)))

    def test_site_template_provided_in_context(self):
        template = SiteTemplate.objects.create(
            name="Test Template",
            primary_color="#112233",
            primary_color_emphasis="#223344",
            accent_color="#445566",
            accent_color_emphasis="#556677",
            support_color="#667788",
            support_color_emphasis="#778899",
            support_text_color="#ffffff",
        )
        site = Site.objects.get(domain="testserver")
        site.template = template
        site.save(update_fields=["template"])

        request = RequestFactory().get("/", HTTP_HOST="testserver")
        request.user = AnonymousUser()
        request.session = self.client.session

        context = nav_links(request)

        self.assertEqual(context["site_template"], template)


class WatchtowerLandingLinkTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.role, _ = NodeRole.objects.get_or_create(name="Watchtower")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": self.role,
            },
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        self.ocpp_app, _ = Application.objects.get_or_create(name="ocpp")
        self.ocpp_module, _ = Module.objects.get_or_create(
            node_role=self.role,
            application=self.ocpp_app,
            path="/ocpp/",
        )
        self.ocpp_module.create_landings()
        feature, _ = NodeFeature.objects.get_or_create(
            slug="rfid-scanner", defaults={"display": "RFID Scanner"}
        )
        node = Node.get_local()
        if node:
            NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)

    def _get_ocpp_module(self, response):
        for module in response.context["nav_modules"]:
            if module.path == "/ocpp/":
                return module
        return None

    def test_ocpp_landings_present_for_anonymous_users(self):
        response = self.client.get(reverse("pages:index"))
        ocpp_module = self._get_ocpp_module(response)
        self.assertIsNotNone(ocpp_module)
        landing_by_label = {
            landing.label: landing for landing in ocpp_module.enabled_landings
        }
        expected_landings = {
            "CPMS Online Dashboard": "/ocpp/cpms/dashboard/",
            "Net Monitor Console": "/ocpp/net-monitor/",
            "Maintenance Request": "/ocpp/maintenance/request/",
            "Charge Point Simulator": "/ocpp/evcs/simulator/",
            "Identity Validator": "/ocpp/rfid/validator/",
        }
        for label, path in expected_landings.items():
            with self.subTest(label=label):
                landing = landing_by_label.get(label)
                self.assertIsNotNone(landing)
                self.assertEqual(landing.path, path)
                self.assertTrue(path.startswith("/"))
                resolve(path)

    def test_simulator_requires_login(self):
        response = self.client.get(reverse("pages:index"))
        ocpp_module = self._get_ocpp_module(response)
        self.assertIsNotNone(ocpp_module)
        locked_landings = {
            landing.label: landing
            for landing in ocpp_module.enabled_landings
            if getattr(landing, "nav_is_locked", False)
        }
        simulator = locked_landings.get("Charge Point Simulator")
        self.assertIsNotNone(simulator)
        self.assertTrue(simulator.nav_is_locked)
        maintenance_request = locked_landings.get("Maintenance Request")
        self.assertIsNotNone(maintenance_request)
        self.assertTrue(maintenance_request.nav_is_locked)
        net_monitor = locked_landings.get("Net Monitor Console")
        self.assertIsNotNone(net_monitor)
        self.assertTrue(net_monitor.nav_is_locked)

class StaffNavVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "localhost", "address": "127.0.0.1", "role": role},
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        app = Application.objects.create(name="ocpp")
        Module.objects.create(node_role=role, application=app, path="/ocpp/")
        User = get_user_model()
        self.user = User.objects.create_user("user", password="pw")
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)

    def test_nonstaff_pill_hidden(self):
        self.client.login(username="user", password="pw")
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'href="/ocpp/cpms/dashboard/"')

    def test_staff_sees_pill(self):
        self.client.login(username="staff", password="pw")
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'href="/ocpp/cpms/dashboard/"')


class ModuleAdminReloadActionTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pw",
        )
        self.client.force_login(self.superuser)
        self.role, _ = NodeRole.objects.get_or_create(name="Watchtower")
        Application.objects.get_or_create(name="ocpp")
        Application.objects.get_or_create(name="awg")
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )

    def _post_reload(self):
        changelist_url = reverse("admin:pages_module_changelist")
        self.client.get(changelist_url)
        csrf_cookie = self.client.cookies.get("csrftoken")
        token = csrf_cookie.value if csrf_cookie else ""
        return self.client.post(
            reverse("admin:pages_module_reload_default_modules"),
            {"csrfmiddlewaretoken": token},
            follow=True,
        )

    def test_reload_restores_missing_modules_and_landings(self):
        Module.objects.filter(node_role=self.role).delete()
        Landing.objects.filter(module__node_role=self.role).delete()

        response = self._post_reload()
        self.assertEqual(response.status_code, 200)

        chargers = Module.objects.get(node_role=self.role, path="/ocpp/")
        calculators = Module.objects.get(node_role=self.role, path="/awg/")

        self.assertEqual(chargers.menu, "Charge Points")
        self.assertEqual(calculators.menu, "")
        self.assertFalse(getattr(chargers, "is_deleted", False))
        self.assertFalse(getattr(calculators, "is_deleted", False))

        charger_landings = set(
            Landing.objects.filter(module=chargers).values_list("path", flat=True)
        )
        self.assertSetEqual(
            charger_landings,
            {
                "/ocpp/cpms/dashboard/",
                "/ocpp/net-monitor/",
                "/ocpp/evcs/simulator/",
                "/ocpp/rfid/validator/",
            },
        )

        calculator_landings = set(
            Landing.objects.filter(module=calculators).values_list(
                "path", flat=True
            )
        )
        self.assertSetEqual(
            calculator_landings,
            {"/awg/", "/awg/energy-tariff/", "/awg/future-event/"},
        )

    def test_reload_is_idempotent(self):
        self._post_reload()
        module_count = Module.objects.filter(node_role=self.role).count()
        landing_count = Landing.objects.filter(module__node_role=self.role).count()

        self._post_reload()

        self.assertEqual(
            Module.objects.filter(node_role=self.role).count(), module_count
        )
        self.assertEqual(
            Landing.objects.filter(module__node_role=self.role).count(),
            landing_count,
        )


class ApplicationModelTests(TestCase):
    def test_path_defaults_to_slugified_name(self):
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "localhost", "address": "127.0.0.1", "role": role},
        )
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        app = Application.objects.create(name="core")
        site_app = Module.objects.create(node_role=role, application=app)
        self.assertEqual(site_app.path, "/core/")

    def test_installed_flag_false_when_missing(self):
        app = Application.objects.create(name="missing")
        self.assertFalse(app.installed)

    def test_verbose_name_property(self):
        app = Application.objects.create(name="ocpp")
        config = django_apps.get_app_config("ocpp")
        self.assertEqual(app.verbose_name, config.verbose_name)


class ApplicationAdminFormTests(TestCase):
    def test_name_field_uses_local_apps(self):
        admin_instance = ApplicationAdmin(Application, admin.site)
        form = admin_instance.get_form(request=None)()
        choices = [choice[0] for choice in form.fields["name"].choices]
        self.assertIn("core", choices)


class ApplicationAdminDisplayTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="app-admin", password="pwd", email="admin@example.com"
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_changelist_shows_verbose_name(self):
        Application.objects.create(name="ocpp")
        resp = self.client.get(reverse("admin:pages_application_changelist"))
        config = django_apps.get_app_config("ocpp")
        self.assertContains(resp, config.verbose_name)

    def test_changelist_shows_description(self):
        Application.objects.create(
            name="awg", description="Power, Energy and Cost calculations."
        )
        resp = self.client.get(reverse("admin:pages_application_changelist"))
        self.assertContains(resp, "Power, Energy and Cost calculations.")


class UserManualAdminFormTests(TestCase):
    def setUp(self):
        self.manual = UserManual.objects.create(
            slug="manual-one",
            title="Manual One",
            description="Test manual",
            languages="en",
            content_html="<p>Manual</p>",
            content_pdf=base64.b64encode(b"initial").decode("ascii"),
        )

    def test_widget_uses_slug_for_download(self):
        admin_instance = UserManualAdmin(UserManual, admin.site)
        form_class = admin_instance.get_form(request=None, obj=self.manual)
        form = form_class(instance=self.manual)
        field = form.fields["content_pdf"]
        self.assertEqual(field.widget.download_name, f"{self.manual.slug}.pdf")
        self.assertEqual(field.widget.content_type, "application/pdf")

    def test_upload_encodes_content_pdf(self):
        admin_instance = UserManualAdmin(UserManual, admin.site)
        form_class = admin_instance.get_form(request=None, obj=self.manual)
        payload = {
            "slug": self.manual.slug,
            "title": self.manual.title,
            "description": self.manual.description,
            "languages": self.manual.languages,
            "content_html": self.manual.content_html,
            "pdf_orientation": self.manual.pdf_orientation,
        }
        upload = SimpleUploadedFile("manual.pdf", b"PDF data")
        form = form_class(data=payload, files={"content_pdf": upload}, instance=self.manual)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(
            form.cleaned_data["content_pdf"],
            base64.b64encode(b"PDF data").decode("ascii"),
        )

    def test_initial_base64_preserved_without_upload(self):
        admin_instance = UserManualAdmin(UserManual, admin.site)
        form_class = admin_instance.get_form(request=None, obj=self.manual)
        payload = {
            "slug": self.manual.slug,
            "title": self.manual.title,
            "description": self.manual.description,
            "languages": self.manual.languages,
            "content_html": self.manual.content_html,
            "pdf_orientation": self.manual.pdf_orientation,
        }
        form = form_class(data=payload, files={}, instance=self.manual)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["content_pdf"], self.manual.content_pdf)


class UserManualModelTests(TestCase):
    def _build_manual(self, **overrides):
        defaults = {
            "slug": "manual-model-test",
            "title": "Manual Model",
            "description": "Manual description",
            "languages": "en",
            "content_html": "<p>Manual</p>",
            "content_pdf": base64.b64encode(b"initial").decode("ascii"),
        }
        defaults.update(overrides)
        return UserManual(**defaults)

    def test_save_encodes_uploaded_file(self):
        upload = SimpleUploadedFile("manual.pdf", b"PDF data")
        manual = self._build_manual(slug="manual-upload", content_pdf=upload)
        manual.save()
        manual.refresh_from_db()
        self.assertEqual(
            manual.content_pdf,
            base64.b64encode(b"PDF data").decode("ascii"),
        )

    def test_save_encodes_raw_bytes(self):
        manual = self._build_manual(slug="manual-bytes", content_pdf=b"PDF raw")
        manual.save()
        manual.refresh_from_db()
        self.assertEqual(
            manual.content_pdf,
            base64.b64encode(b"PDF raw").decode("ascii"),
        )

    def test_save_strips_data_uri_prefix(self):
        encoded = base64.b64encode(b"PDF data").decode("ascii")
        data_uri = f"data:application/pdf;base64,{encoded}"
        manual = self._build_manual(slug="manual-data-uri", content_pdf=data_uri)
        manual.save()
        manual.refresh_from_db()
        self.assertEqual(manual.content_pdf, encoded)


class LandingCreationTests(TestCase):
    def setUp(self):
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={"hostname": "localhost", "address": "127.0.0.1", "role": role},
        )
        self.app, _ = Application.objects.get_or_create(name="pages")
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": ""}
        )
        self.role = role

    def test_landings_created_on_module_creation(self):
        module = Module.objects.create(
            node_role=self.role, application=self.app, path="/"
        )
        self.assertTrue(module.landings.filter(path="/").exists())


class LandingFixtureTests(TestCase):
    def test_watchtower_fixture_loads_without_duplicates(self):
        from glob import glob

        NodeRole.objects.get_or_create(name="Watchtower")
        fixtures = glob(
            str(Path(settings.BASE_DIR, "pages", "fixtures", "watchtower__*.json"))
        )
        fixtures = sorted(
            fixtures,
            key=lambda path: (
                0 if "__application_" in path else 1 if "__module_" in path else 2
            ),
        )
        call_command("loaddata", *fixtures)
        call_command("loaddata", *fixtures)
        module = Module.objects.get(path="/ocpp/", node_role__name="Watchtower")
        module.create_landings()
        self.assertEqual(
            module.landings.filter(path="/ocpp/rfid/validator/").count(), 1
        )


class AllowedHostSubnetTests(TestCase):
    def setUp(self):
        self.client = Client()
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "pages"}
        )

    @override_settings(ALLOWED_HOSTS=["10.42.0.0/16", "192.168.0.0/16"])
    def test_private_network_hosts_allowed(self):
        resp = self.client.get(reverse("pages:index"), HTTP_HOST="10.42.1.5")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reverse("pages:index"), HTTP_HOST="192.168.2.3")
        self.assertEqual(resp.status_code, 200)

    @override_settings(ALLOWED_HOSTS=["10.42.0.0/16"])
    def test_host_outside_subnets_disallowed(self):
        resp = self.client.get(reverse("pages:index"), HTTP_HOST="11.0.0.1")
        self.assertEqual(resp.status_code, 400)


class RFIDPageTests(TestCase):
    def setUp(self):
        self.client = Client()
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "pages"}
        )
        User = get_user_model()
        self.user = User.objects.create_user("rfid-user", password="pwd")

    def test_page_redirects_when_anonymous(self):
        resp = self.client.get(reverse("rfid-reader"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("pages:login"), resp.url)

    def test_page_renders_for_authenticated_user(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("rfid-reader"))
        self.assertContains(resp, "Scanner ready")


class FaviconTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)

    def _png(self, name):
        data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg=="
        )
        return SimpleUploadedFile(name, data, content_type="image/png")

    def test_site_app_favicon_preferred_over_site(self):
        with override_settings(MEDIA_ROOT=self.tmpdir):
            role, _ = NodeRole.objects.get_or_create(name="Terminal")
            Node.objects.update_or_create(
                mac_address=Node.get_current_mac(),
                defaults={
                    "hostname": "localhost",
                    "address": "127.0.0.1",
                    "role": role,
                },
            )
            site, _ = Site.objects.update_or_create(
                id=1, defaults={"domain": "testserver", "name": ""}
            )
            SiteBadge.objects.create(
                site=site, badge_color="#28a745", favicon=self._png("site.png")
            )
            app = Application.objects.create(name="readme")
            Module.objects.create(
                node_role=role,
                application=app,
                path="/",
                is_default=True,
                favicon=self._png("app.png"),
            )
            resp = self.client.get(reverse("pages:index"))
            self.assertContains(resp, "app.png")

    def test_site_favicon_used_when_app_missing(self):
        with override_settings(MEDIA_ROOT=self.tmpdir):
            role, _ = NodeRole.objects.get_or_create(name="Terminal")
            Node.objects.update_or_create(
                mac_address=Node.get_current_mac(),
                defaults={
                    "hostname": "localhost",
                    "address": "127.0.0.1",
                    "role": role,
                },
            )
            site, _ = Site.objects.update_or_create(
                id=1, defaults={"domain": "testserver", "name": ""}
            )
            SiteBadge.objects.create(
                site=site, badge_color="#28a745", favicon=self._png("site.png")
            )
            app = Application.objects.create(name="readme")
            Module.objects.create(
                node_role=role, application=app, path="/", is_default=True
            )
            resp = self.client.get(reverse("pages:index"))
            self.assertContains(resp, "site.png")

    def test_default_favicon_used_when_none_defined(self):
        with override_settings(MEDIA_ROOT=self.tmpdir):
            role, _ = NodeRole.objects.get_or_create(name="Terminal")
            Node.objects.update_or_create(
                mac_address=Node.get_current_mac(),
                defaults={
                    "hostname": "localhost",
                    "address": "127.0.0.1",
                    "role": role,
                },
            )
            Site.objects.update_or_create(
                id=1, defaults={"domain": "testserver", "name": ""}
            )
            resp = self.client.get(reverse("pages:index"))
            b64 = (
                Path(settings.BASE_DIR)
                .joinpath("pages", "fixtures", "data", "favicon.txt")
                .read_text()
                .strip()
            )
            self.assertContains(resp, b64)

    def test_control_nodes_use_silver_favicon(self):
        with override_settings(MEDIA_ROOT=self.tmpdir):
            role, _ = NodeRole.objects.get_or_create(name="Control")
            Node.objects.update_or_create(
                mac_address=Node.get_current_mac(),
                defaults={
                    "hostname": "localhost",
                    "address": "127.0.0.1",
                    "role": role,
                },
            )
            Site.objects.update_or_create(
                id=1, defaults={"domain": "testserver", "name": ""}
            )
            resp = self.client.get(reverse("pages:index"))
            b64 = (
                Path(settings.BASE_DIR)
                .joinpath("pages", "fixtures", "data", "favicon_control.txt")
                .read_text()
                .strip()
            )
            self.assertContains(resp, b64)

    def test_watchtower_nodes_use_goldenrod_favicon(self):
        with override_settings(MEDIA_ROOT=self.tmpdir):
            role, _ = NodeRole.objects.get_or_create(name="Watchtower")
            Node.objects.update_or_create(
                mac_address=Node.get_current_mac(),
                defaults={
                    "hostname": "localhost",
                    "address": "127.0.0.1",
                    "role": role,
                },
            )
            Site.objects.update_or_create(
                id=1, defaults={"domain": "testserver", "name": ""}
            )
            resp = self.client.get(reverse("pages:index"))
            b64 = (
                Path(settings.BASE_DIR)
                .joinpath("pages", "fixtures", "data", "favicon_watchtower.txt")
                .read_text()
                .strip()
            )
            self.assertContains(resp, b64)

    def test_satellite_nodes_use_silver_favicon(self):
        with override_settings(MEDIA_ROOT=self.tmpdir):
            role, _ = NodeRole.objects.get_or_create(name="Satellite")
            Node.objects.update_or_create(
                mac_address=Node.get_current_mac(),
                defaults={
                    "hostname": "localhost",
                    "address": "127.0.0.1",
                    "role": role,
                },
            )
            Site.objects.update_or_create(
                id=1, defaults={"domain": "testserver", "name": ""}
            )
            resp = self.client.get(reverse("pages:index"))
            b64 = (
                Path(settings.BASE_DIR)
                .joinpath("pages", "fixtures", "data", "favicon_satellite.txt")
                .read_text()
                .strip()
            )
            self.assertContains(resp, b64)


class FavoriteTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="favadmin", password="pwd", email="fav@example.com"
        )
        ReleaseManager.objects.create(user=self.user)
        self.client.force_login(self.user)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )
        from nodes.models import Node, NodeRole

        terminal_role, _ = NodeRole.objects.get_or_create(name="Terminal")
        self.terminal_role = terminal_role
        self.node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": terminal_role,
            },
        )
        ContentType.objects.clear_cache()

    def test_add_favorite(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        next_url = reverse("admin:pages_application_changelist")
        url = (
            reverse("admin:favorite_toggle", args=[ct.id]) + f"?next={quote(next_url)}"
        )
        resp = self.client.post(url, {"custom_label": "Apps", "user_data": "on"})
        self.assertRedirects(resp, next_url)
        fav = Favorite.objects.get(user=self.user, content_type=ct)
        self.assertEqual(fav.custom_label, "Apps")
        self.assertTrue(fav.user_data)

    def test_add_favorite_get_returns_not_allowed(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        next_url = reverse("admin:pages_application_changelist")
        url = (
            reverse("admin:favorite_toggle", args=[ct.id]) + f"?next={quote(next_url)}"
        )

        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 405)
        self.assertFalse(
            Favorite.objects.filter(user=self.user, content_type=ct).exists()
        )

    def test_add_favorite_post_sets_defaults_and_redirects(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        next_url = reverse("admin:pages_application_changelist")
        url = reverse("admin:favorite_toggle", args=[ct.id])

        resp = self.client.post(url, {"next": next_url, "user_data": "on"})

        self.assertRedirects(resp, next_url)
        fav = Favorite.objects.get(user=self.user, content_type=ct)
        self.assertTrue(fav.user_data)
        self.assertEqual(fav.priority, 0)
        self.assertEqual(fav.custom_label, "")

    def test_add_favorite_with_priority(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        url = reverse("admin:favorite_toggle", args=[ct.id])
        resp = self.client.post(url, {"priority": "7"})
        self.assertRedirects(resp, reverse("admin:index"))
        fav = Favorite.objects.get(user=self.user, content_type=ct)
        self.assertEqual(fav.priority, 7)

    def test_cancel_link_uses_next(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        next_url = reverse("admin:pages_application_changelist")
        Favorite.objects.create(user=self.user, content_type=ct)
        url = (
            reverse("admin:favorite_toggle", args=[ct.id]) + f"?next={quote(next_url)}"
        )
        resp = self.client.get(url)
        self.assertContains(resp, f'href="{next_url}"')

    def test_rejects_external_next_url(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        next_url = "https://malicious.example.com/admin/"
        url = (
            reverse("admin:favorite_toggle", args=[ct.id]) + f"?next={quote(next_url)}"
        )

        admin_index = reverse("admin:index")

        resp = self.client.post(url, {"custom_label": "Apps"})
        self.assertRedirects(resp, admin_index)

        Favorite.objects.filter(user=self.user, content_type=ct).delete()

        resp = self.client.get(url)
        self.assertRedirects(resp, admin_index)

    def test_existing_favorite_shows_update_form(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        favorite = Favorite.objects.create(
            user=self.user, content_type=ct, custom_label="Apps", user_data=True
        )
        url = reverse("admin:favorite_toggle", args=[ct.id])
        resp = self.client.get(url)
        self.assertContains(resp, "Update Favorite")
        self.assertContains(resp, "value=\"Apps\"")
        self.assertContains(resp, "checked")
        self.assertContains(resp, "name=\"remove\"")

        resp = self.client.post(url, {"custom_label": "Apps Updated"})
        self.assertRedirects(resp, reverse("admin:index"))
        favorite.refresh_from_db()
        self.assertEqual(favorite.custom_label, "Apps Updated")
        self.assertFalse(favorite.user_data)

    def test_remove_existing_favorite_from_toggle(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        Favorite.objects.create(user=self.user, content_type=ct)
        url = reverse("admin:favorite_toggle", args=[ct.id])
        resp = self.client.post(url, {"remove": "1"})
        self.assertRedirects(resp, reverse("admin:index"))
        self.assertFalse(Favorite.objects.filter(user=self.user, content_type=ct).exists())

    def test_update_user_data_from_list(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        fav = Favorite.objects.create(user=self.user, content_type=ct)
        url = reverse("admin:favorite_list")
        resp = self.client.post(url, {"user_data": [str(fav.pk)]})
        self.assertRedirects(resp, url)
        fav.refresh_from_db()
        self.assertTrue(fav.user_data)

    def test_update_priority_from_list(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        fav = Favorite.objects.create(user=self.user, content_type=ct, priority=3)
        url = reverse("admin:favorite_list")
        resp = self.client.post(url, {f"priority_{fav.pk}": "12"})
        self.assertRedirects(resp, url)
        fav.refresh_from_db()
        self.assertEqual(fav.priority, 12)

    def test_dashboard_includes_favorites_and_user_data(self):
        fav_ct = ContentType.objects.get_by_natural_key("pages", "application")
        Favorite.objects.create(
            user=self.user, content_type=fav_ct, custom_label="Apps"
        )
        NodeRole.objects.create(name="DataRole", is_user_data=True)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, reverse("admin:pages_application_changelist"))
        self.assertContains(resp, reverse("admin:nodes_noderole_changelist"))

    def test_dashboard_shows_open_lead_badge(self):
        InviteLead.objects.create(email="open1@example.com")
        InviteLead.objects.create(email="open2@example.com")
        closed = InviteLead.objects.create(email="closed@example.com")
        closed.status = InviteLead.Status.CLOSED
        closed.save(update_fields=["status"])
        assigned = InviteLead.objects.create(email="assigned@example.com")
        assigned.status = InviteLead.Status.ASSIGNED
        assigned.save(update_fields=["status"])

        resp = self.client.get(reverse("admin:index"))
        content = resp.content.decode()

        self.assertIn('badge-counter lead-open-badge', content)
        self.assertIn('title="2 open leads"', content)
        self.assertIn('aria-label="2 open leads"', content)

    def test_dashboard_shows_rfid_release_badge(self):
        RFID.objects.create(rfid="RFID0001", released=True, allowed=True)
        RFID.objects.create(rfid="RFID0002", released=True, allowed=False)

        resp = self.client.get(reverse("admin:index"))

        expected = "1 / 2"
        badge_label = gettext(
            "%(released_allowed)s released and allowed RFIDs out of %(registered)s registered RFIDs"
        ) % {"released_allowed": 1, "registered": 2}

        self.assertContains(resp, expected)
        self.assertContains(resp, "badge-counter rfid-release-badge")
        self.assertContains(resp, f'title="{badge_label}"')
        self.assertContains(resp, f'aria-label="{badge_label}"')

    def test_dashboard_shows_charge_point_availability_badge(self):
        Charger.objects.create(
            charger_id="CP-001", connector_id=1, last_status="Available"
        )
        Charger.objects.create(charger_id="CP-002", last_status="Available")
        Charger.objects.create(
            charger_id="CP-003", connector_id=1, last_status="Unavailable"
        )

        resp = self.client.get(reverse("admin:index"))

        expected = "1 / 2"
        badge_label = gettext(
            "%(available)s chargers reporting Available status with a CP number, out of %(total)s total Available chargers. %(missing)s Available chargers are missing a connector letter."
        ) % {"available": 1, "total": 2, "missing": 1}

        self.assertContains(resp, expected)
        self.assertContains(resp, "badge-counter charger-availability-badge")
        self.assertContains(resp, f'title="{badge_label}"')
        self.assertContains(resp, f'aria-label="{badge_label}"')

    def test_dashboard_shows_node_known_badge(self):
        Node.objects.create(
            hostname="upstream-node",
            address="10.0.0.2",
            mac_address="11:22:33:44:55:66",
            role=self.terminal_role,
            current_relation=Node.Relation.UPSTREAM,
        )

        resp = self.client.get(reverse("admin:index"))

        content = resp.content.decode()
        self.assertIn('badge-counter node-count-badge', content)
        self.assertIn('title="2 nodes known to this deployment"', content)
        self.assertIn('aria-label="2 nodes known to this deployment"', content)
        self.assertIn('>2<', content)

        badge_index = content.index("node-count-badge")
        rule_index = content.index("model-rule-status")
        self.assertLess(badge_index, rule_index)

    def test_badge_counters_use_cache_and_invalidate(self):
        node_ct = ContentType.objects.get_by_natural_key("nodes", "node")

        counters = admin_extras.badge_counters(Context({}), "nodes", "Node")
        self.assertTrue(counters)
        initial_value = counters[0]["primary"]

        Node.objects.create(
            hostname="cached-node",
            address="10.0.0.3",
            mac_address="22:33:44:55:66:77",
            role=self.terminal_role,
            current_relation=Node.Relation.UPSTREAM,
        )

        cached = admin_extras.badge_counters(Context({}), "nodes", "Node")
        self.assertEqual(cached[0]["primary"], initial_value)

        BadgeCounter.invalidate_model_cache(node_ct)
        refreshed = admin_extras.badge_counters(Context({}), "nodes", "Node")
        self.assertNotEqual(refreshed[0]["primary"], initial_value)

    def test_dashboard_charge_point_badge_ignores_aggregator(self):
        Charger.objects.create(charger_id="CP-AGG", last_status="Available")
        Charger.objects.create(
            charger_id="CP-AGG", connector_id=1, last_status="Available"
        )
        Charger.objects.create(
            charger_id="CP-AGG", connector_id=2, last_status="Available"
        )

        resp = self.client.get(reverse("admin:index"))

        expected = "2 / 2"
        badge_label = gettext(
            "%(available)s chargers reporting Available status with a CP number."
        ) % {"available": 2}

        self.assertContains(resp, expected)
        self.assertContains(resp, "badge-counter charger-availability-badge")
        self.assertContains(resp, f'title="{badge_label}"')
        self.assertContains(resp, f'aria-label="{badge_label}"')

    def test_nav_sidebar_hides_dashboard_badges(self):
        InviteLead.objects.create(email="open@example.com")
        RFID.objects.create(rfid="RFID0003", released=True, allowed=True)

        resp = self.client.get(reverse("admin:teams_invitelead_changelist"))

        self.assertNotContains(resp, "badge-counter")

    def test_dashboard_includes_google_calendar_module(self):
        GoogleCalendarProfile.objects.create(
            user=self.user,
            calendar_id="calendar@example.com",
        )
        with (
            patch(
                "pages.templatetags.admin_extras.GoogleCalendarProfile.fetch_events",
                return_value=[],
            ),
            patch(
                "pages.templatetags.admin_extras.GoogleCalendarProfile.get_display_name",
                return_value="Calendar",
            ),
            patch(
                "pages.templatetags.admin_extras.GoogleCalendarProfile.build_calendar_url",
                return_value="",
            ),
        ):
            resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Calendar")

    def test_favorite_ct_id_recreates_missing_content_type(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        ct.delete()
        from pages.templatetags.favorites import favorite_ct_id

        new_id = favorite_ct_id("pages", "Application")
        self.assertIsNotNone(new_id)
        self.assertTrue(
            ContentType.objects.filter(
                pk=new_id, app_label="pages", model="application"
            ).exists()
        )

    def test_dashboard_uses_change_label(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        Favorite.objects.create(user=self.user, content_type=ct)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Change Applications")
        self.assertContains(resp, 'target="_blank" rel="noopener noreferrer"')


class AdminIndexQueryRegressionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = Client()
        self.user = User.objects.create_superuser(
            username="queryadmin", password="pwd", email="query@example.com"
        )
        self.client.force_login(self.user)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )
        favorite_cts = [
            ContentType.objects.get_for_model(Application),
            ContentType.objects.get_for_model(Landing),
        ]
        for ct in favorite_cts:
            Favorite.objects.create(user=self.user, content_type=ct)

    def _render_admin_and_count_queries(self):
        url = reverse("admin:index")
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        sql_statements = [query["sql"].lower() for query in ctx.captured_queries]
        ct_queries = [sql for sql in sql_statements if '"django_content_type"' in sql]
        favorite_queries = [sql for sql in sql_statements if '"pages_favorite"' in sql]
        return len(ct_queries), len(favorite_queries)

    def test_admin_index_queries_constant_with_more_models(self):
        site = admin.site
        original_registry = site._registry.copy()
        registry_items = list(original_registry.items())
        if len(registry_items) < 2:
            self.skipTest("Not enough registered admin models for regression test")

        for model in original_registry.keys():
            ContentType.objects.get_for_model(model)

        try:
            site._registry = dict(registry_items[:1])
            baseline_ct_queries, baseline_favorite_queries = (
                self._render_admin_and_count_queries()
            )

            expanded_limit = min(len(registry_items), 5)
            site._registry = dict(registry_items[:expanded_limit])
            expanded_ct_queries, expanded_favorite_queries = (
                self._render_admin_and_count_queries()
            )
        finally:
            site._registry = original_registry

        self.assertEqual(expanded_ct_queries, baseline_ct_queries)
        self.assertEqual(expanded_favorite_queries, baseline_favorite_queries)


class AdminActionListTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.objects.filter(username="action-admin").delete()
        self.user = User.objects.create_superuser(
            username="action-admin",
            password="pwd",
            email="action@example.com",
        )
        self.factory = RequestFactory()

    def test_profile_actions_available_without_selection(self):
        from pages.templatetags.admin_extras import model_admin_actions

        request = self.factory.get("/")
        request.user = self.user
        context = {"request": request}

        registered = [
            (model._meta.app_label, model._meta.object_name)
            for model, admin_instance in admin.site._registry.items()
            if isinstance(admin_instance, ProfileAdminMixin)
        ]

        for app_label, object_name in registered:
            with self.subTest(model=f"{app_label}.{object_name}"):
                actions = model_admin_actions(context, app_label, object_name)
                labels = {action["label"] for action in actions}
                self.assertIn("Active Profile", labels)

    def test_quote_report_link_available(self):
        from pages.templatetags.admin_extras import model_admin_actions

        request = self.factory.get("/")
        request.user = self.user
        context = {"request": request}

        actions = model_admin_actions(context, "core", "OdooProfile")
        labels = {action["label"] for action in actions}
        self.assertIn("Quote Report", labels)
        url = next(
            action["url"]
            for action in actions
            if action["label"] == "Quote Report"
        )
        self.assertEqual(
            url,
            reverse(
                "admin:core_odooprofile_actions",
                kwargs={"tool": "generate_quote_report"},
            ),
        )

    def test_send_net_message_link_available(self):
        from pages.templatetags.admin_extras import model_admin_actions

        request = self.factory.get("/")
        request.user = self.user
        context = {"request": request}

        actions = model_admin_actions(context, "nodes", "NetMessage")
        labels = {action["label"] for action in actions}
        self.assertIn("Send Net Message", labels)
        url = next(
            action["url"]
            for action in actions
            if action["label"] == "Send Net Message"
        )
        self.assertEqual(url, reverse("admin:nodes_netmessage_send"))

    def test_queryset_actions_are_omitted(self):
        from django.contrib.contenttypes.models import ContentType
        from pages.templatetags.admin_extras import model_admin_actions

        request = self.factory.get("/")
        request.user = self.user
        context = {"request": request}

        class DummyAdmin(admin.ModelAdmin):
            actions = ["needs_selection"]

            def needs_selection(self, request, queryset):
                return None

        model = ContentType
        original_admin = admin.site._registry.get(model)
        if original_admin:
            admin.site.unregister(model)
        try:
            admin.site.register(model, DummyAdmin)
            actions = model_admin_actions(
                context, model._meta.app_label, model._meta.object_name
            )
            self.assertEqual(actions, [])
        finally:
            admin.site.unregister(model)
            if original_admin:
                admin.site.register(model, type(original_admin))


class AdminModelGraphViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="graph-staff", password="pwd", is_staff=True
        )
        Site.objects.update_or_create(id=1, defaults={"name": "Terminal"})
        self.client.force_login(self.user)

    def _mock_graph(self):
        fake_graph = Mock()
        fake_graph.source = "digraph {}"
        fake_graph.engine = "dot"

        def pipe_side_effect(*args, **kwargs):
            fmt = kwargs.get("format") or (args[0] if args else None)
            if fmt == "svg":
                return '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
            if fmt == "pdf":
                return b"%PDF-1.4 mock"
            raise AssertionError(f"Unexpected format: {fmt}")

        fake_graph.pipe.side_effect = pipe_side_effect
        return fake_graph

    def test_model_graph_renders_controls_and_download_link(self):
        url = reverse("admin-model-graph", args=["pages"])
        graph = self._mock_graph()
        with (
            patch("pages.views._build_model_graph", return_value=graph),
            patch("pages.views.shutil.which", return_value="/usr/bin/dot"),
        ):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-model-graph")
        self.assertContains(response, 'data-graph-action="zoom-in"')
        self.assertContains(response, "Download PDF")
        self.assertIn("?format=pdf", response.context_data["download_url"])
        args, kwargs = graph.pipe.call_args
        self.assertEqual(kwargs.get("format"), "svg")
        self.assertEqual(kwargs.get("encoding"), "utf-8")

    def test_model_graph_pdf_download(self):
        url = reverse("admin-model-graph", args=["pages"])
        graph = self._mock_graph()
        with (
            patch("pages.views._build_model_graph", return_value=graph),
            patch("pages.views.shutil.which", return_value="/usr/bin/dot"),
        ):
            response = self.client.get(url, {"format": "pdf"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        app_config = django_apps.get_app_config("pages")
        expected_slug = slugify(app_config.verbose_name) or app_config.label
        self.assertIn(
            f"{expected_slug}-model-graph.pdf", response["Content-Disposition"]
        )
        self.assertEqual(response.content, b"%PDF-1.4 mock")
        args, kwargs = graph.pipe.call_args
        self.assertEqual(kwargs.get("format"), "pdf")


class LanguagePreferenceMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = LanguagePreferenceMiddleware(lambda request: HttpResponse("ok"))

    def test_selected_language_attached_to_request(self):
        request = self.factory.get("/")
        request.session = {}
        with translation.override("it"):
            response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.selected_language_code, "it")
        self.assertEqual(request.selected_language, "it")

    def test_normalizes_request_language_code(self):
        request = self.factory.get("/")
        request.session = {}
        request.LANGUAGE_CODE = "PT_BR"

        response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.selected_language_code, "pt-br")
        self.assertEqual(request.selected_language, "pt-br")


class UserStorySubmissionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse("pages:user-story-submit")
        User = get_user_model()
        self.user = User.objects.create_user(username="feedbacker", password="pwd")
        self.capture_patcher = patch("pages.views.capture_screenshot", autospec=True)
        self.save_patcher = patch("pages.views.save_screenshot", autospec=True)
        self.mock_capture = self.capture_patcher.start()
        self.mock_save = self.save_patcher.start()
        self.mock_capture.return_value = Path("/tmp/fake.png")
        self.mock_save.return_value = None
        self.addCleanup(self.capture_patcher.stop)
        self.addCleanup(self.save_patcher.stop)

    def test_authenticated_submission_defaults_to_username(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            {
                "rating": 5,
                "comments": "Loved the experience!",
                "path": "/wizard/step-1/",
                "take_screenshot": "1",
            },
            HTTP_REFERER="https://example.test/wizard/step-1/",
            HTTP_USER_AGENT="FeedbackBot/1.0",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        story = UserStory.objects.get()
        self.assertEqual(story.name, "feedbacker")
        self.assertEqual(story.rating, 5)
        self.assertEqual(story.path, "/wizard/step-1/")
        self.assertEqual(story.user, self.user)
        self.assertEqual(story.owner, self.user)
        self.assertTrue(story.is_user_data)
        self.assertTrue(story.take_screenshot)
        self.assertEqual(story.status, UserStory.Status.OPEN)
        self.assertEqual(story.referer, "https://example.test/wizard/step-1/")
        self.assertEqual(story.user_agent, "FeedbackBot/1.0")
        self.assertEqual(story.ip_address, "127.0.0.1")
        expected_language = (translation.get_language() or "").split("-")[0]
        self.assertTrue(story.language_code)
        self.assertTrue(
            story.language_code.startswith(expected_language),
            story.language_code,
        )

    def test_submission_records_request_language(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "es"
        with translation.override("es"):
            response = self.client.post(
                self.url,
                {
                    "rating": 4,
                    "comments": "Buena experiencia",
                    "path": "/es/soporte/",
                    "take_screenshot": "1",
                },
                HTTP_ACCEPT_LANGUAGE="es",
            )

        self.assertEqual(response.status_code, 200)
        story = UserStory.objects.get()
        self.assertEqual(story.language_code, "es")

    def test_submission_prefers_original_referer(self):
        self.client.get(
            reverse("pages:index"),
            HTTP_REFERER="https://ads.example/original",
        )
        response = self.client.post(
            self.url,
            {
                "rating": 3,
                "comments": "Works well",
                "path": "/wizard/step-2/",
                "name": "visitor@example.com",
                "take_screenshot": "0",
            },
            HTTP_REFERER="http://testserver/wizard/step-2/",
            HTTP_USER_AGENT="FeedbackBot/2.0",
        )

        self.assertEqual(response.status_code, 200)
        story = UserStory.objects.get()
        self.assertEqual(story.referer, "https://ads.example/original")
        self.assertTrue(story.take_screenshot)
        self.mock_capture.assert_called_once_with("http://testserver/wizard/step-2/")

    def test_screenshot_request_links_saved_sample(self):
        self.client.force_login(self.user)
        screenshot_file = Path("/tmp/fake.png")
        self.mock_capture.return_value = screenshot_file
        sample = ContentSample.objects.create(kind=ContentSample.IMAGE)
        self.mock_save.return_value = sample

        response = self.client.post(
            self.url,
            {
                "rating": 5,
                "comments": "Loved the experience!",
                "path": "/wizard/step-1/",
                "take_screenshot": "1",
            },
            HTTP_REFERER="https://example.test/wizard/step-1/",
        )

        self.assertEqual(response.status_code, 200)
        story = UserStory.objects.get()
        self.assertEqual(story.screenshot, sample)
        self.mock_capture.assert_called_once_with("https://example.test/wizard/step-1/")
        self.mock_save.assert_called_once_with(
            screenshot_file,
            method="USER_STORY",
            user=self.user,
            link_duplicates=True,
        )

    def test_anonymous_submission_uses_provided_email(self):
        response = self.client.post(
            self.url,
            {
                "name": "guest@example.com",
                "rating": 3,
                "comments": "It was fine.",
                "path": "/status/",
                "take_screenshot": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserStory.objects.count(), 1)
        story = UserStory.objects.get()
        self.assertEqual(story.name, "guest@example.com")
        self.assertIsNone(story.user)
        self.assertIsNone(story.owner)
        self.assertEqual(story.comments, "It was fine.")
        self.assertTrue(story.take_screenshot)
        self.assertEqual(story.status, UserStory.Status.OPEN)

    def test_invalid_rating_returns_errors(self):
        response = self.client.post(
            self.url,
            {
                "rating": 7,
                "comments": "Way off the scale",
                "path": "/feedback/",
                "take_screenshot": "1",
            },
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(UserStory.objects.exists())
        self.assertIn("rating", data.get("errors", {}))

    def test_anonymous_submission_without_email_returns_errors(self):
        response = self.client.post(
            self.url,
            {
                "rating": 2,
                "comments": "Could be better.",
                "path": "/feedback/",
                "take_screenshot": "1",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserStory.objects.exists())
        data = response.json()
        self.assertIn("name", data.get("errors", {}))

    def test_anonymous_submission_with_invalid_email_returns_errors(self):
        response = self.client.post(
            self.url,
            {
                "name": "Guest Reviewer",
                "rating": 3,
                "comments": "Needs improvement.",
                "path": "/feedback/",
                "take_screenshot": "1",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserStory.objects.exists())
        data = response.json()
        self.assertIn("name", data.get("errors", {}))

    def test_submission_without_screenshot_request(self):
        response = self.client.post(
            self.url,
            {
                "name": "guest@example.com",
                "rating": 4,
                "comments": "Skip the screenshot, please.",
                "path": "/feedback/",
            },
        )
        self.assertEqual(response.status_code, 200)
        story = UserStory.objects.get()
        self.assertTrue(story.take_screenshot)
        self.assertIsNone(story.owner)
        self.assertIsNone(story.screenshot)
        self.assertEqual(story.status, UserStory.Status.OPEN)
        self.mock_capture.assert_called_once_with("http://testserver/feedback/")
        self.mock_save.assert_not_called()

    def test_rate_limit_blocks_repeated_submissions(self):
        payload = {
            "name": "guest@example.com",
            "rating": 4,
            "comments": "Pretty good",
            "path": "/feedback/",
            "take_screenshot": "1",
        }
        first = self.client.post(self.url, payload)
        self.assertEqual(first.status_code, 200)
        second = self.client.post(self.url, payload)
        self.assertEqual(second.status_code, 429)
        data = second.json()
        self.assertFalse(data["success"])
        self.assertIn("__all__", data.get("errors", {}))
        self.assertIn("5", data["errors"]["__all__"][0])


class UserStoryIssueAutomationTests(TestCase):
    def setUp(self):
        self.lock_dir = Path(settings.BASE_DIR) / "locks"
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.lock_dir / "celery.lck"
        User = get_user_model()
        self.user = User.objects.create_user(
            username="feedback_user", password="pwd"
        )

    def tearDown(self):
        self.lock_file.unlink(missing_ok=True)

    def test_low_rating_story_enqueues_issue_creation_when_celery_enabled(self):
        self.lock_file.write_text("")

        with patch("pages.models.create_user_story_github_issue.delay") as mock_delay:
            story = UserStory.objects.create(
                path="/feedback/",
                rating=2,
                comments="Needs work",
                take_screenshot=False,
                user=self.user,
            )

        mock_delay.assert_called_once_with(story.pk)

    def test_low_rating_story_without_user_does_not_enqueue_issue(self):
        self.lock_file.write_text("")

        with patch("pages.models.create_user_story_github_issue.delay") as mock_delay:
            UserStory.objects.create(
                path="/feedback/",
                rating=2,
                comments="Needs work",
                take_screenshot=False,
            )

        mock_delay.assert_not_called()

    def test_five_star_story_does_not_enqueue_issue(self):
        self.lock_file.write_text("")

        with patch("pages.models.create_user_story_github_issue.delay") as mock_delay:
            UserStory.objects.create(
                path="/feedback/",
                rating=5,
                comments="Great!",
                take_screenshot=True,
            )

        mock_delay.assert_not_called()

    def test_low_rating_story_skips_when_celery_disabled(self):
        self.lock_file.unlink(missing_ok=True)

        with patch("pages.models.create_user_story_github_issue.delay") as mock_delay:
            UserStory.objects.create(
                path="/feedback/",
                rating=1,
                comments="Not good",
                take_screenshot=False,
                user=self.user,
            )

        mock_delay.assert_not_called()


class UserStoryAdminActionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        User = get_user_model()
        self.admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pwd",
        )
        self.story = UserStory.objects.create(
            path="/",
            name="Feedback",
            rating=4,
            comments="Helpful notes",
            take_screenshot=True,
        )
        self.story.language_code = "es"
        self.story.save(update_fields=["language_code"])
        self.admin = UserStoryAdmin(UserStory, admin.site)

    def _build_request(self):
        request = self.factory.post("/admin/pages/userstory/")
        request.user = self.admin_user
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))
        return request

    @patch("pages.models.github_issues.create_issue")
    def test_create_github_issues_action_updates_issue_fields(self, mock_create_issue):
        response = MagicMock()
        response.json.return_value = {
            "html_url": "https://github.com/example/repo/issues/123",
            "number": 123,
        }
        mock_create_issue.return_value = response

        request = self._build_request()
        queryset = UserStory.objects.filter(pk=self.story.pk)
        self.admin.create_github_issues(request, queryset)

        self.story.refresh_from_db()
        self.assertEqual(self.story.github_issue_number, 123)
        self.assertEqual(
            self.story.github_issue_url,
            "https://github.com/example/repo/issues/123",
        )

        mock_create_issue.assert_called_once()
        args, kwargs = mock_create_issue.call_args
        self.assertIn("Feedback for", args[0])
        self.assertIn("**Rating:**", args[1])
        self.assertIn("**Language:**", args[1])
        self.assertIn("(es)", args[1])
        self.assertEqual(kwargs.get("labels"), ["feedback"])
        self.assertEqual(
            kwargs.get("fingerprint"), f"user-story:{self.story.pk}"
        )

    @patch("pages.models.github_issues.create_issue")
    def test_create_github_issues_action_skips_existing_issue(self, mock_create_issue):
        self.story.github_issue_url = "https://github.com/example/repo/issues/5"
        self.story.github_issue_number = 5
        self.story.save(update_fields=["github_issue_url", "github_issue_number"])

        request = self._build_request()
        queryset = UserStory.objects.filter(pk=self.story.pk)
        self.admin.create_github_issues(request, queryset)

        mock_create_issue.assert_not_called()

    def test_create_github_issues_action_links_to_credentials_when_missing(self):
        request = self._build_request()
        queryset = UserStory.objects.filter(pk=self.story.pk)

        mock_url = "/admin/core/releasemanager/"
        with (
            patch(
                "pages.admin.reverse", return_value=mock_url
            ) as mock_reverse,
            patch.object(
                UserStory,
                "create_github_issue",
                side_effect=RuntimeError("GitHub token is not configured"),
            ),
        ):
            self.admin.create_github_issues(request, queryset)

        messages_list = list(request._messages)
        self.assertTrue(messages_list)

        opts = ReleaseManager._meta
        mock_reverse.assert_called_once_with(
            f"{self.admin.admin_site.name}:{opts.app_label}_{opts.model_name}_changelist"
        )
        self.assertTrue(
            any(mock_url in message.message for message in messages_list),
        )
        self.assertTrue(
            any("Configure GitHub credentials" in message.message for message in messages_list),
        )
        self.assertTrue(
            any(message.level == messages.ERROR for message in messages_list),
        )


class ClientReportLiveUpdateTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_client_report_includes_interval(self):
        resp = self.client.get(reverse("pages:client-report"))
        self.assertEqual(resp.wsgi_request.live_update_interval, 5)
        self.assertContains(resp, "setInterval(() => location.reload()")

    def test_client_report_download_disables_refresh(self):
        User = get_user_model()
        user = User.objects.create_user(username="download-user", password="pwd")
        report = ClientReport.objects.create(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            data={},
            owner=user,
            disable_emails=True,
            language="en",
            title="",
        )

        self.client.force_login(user)
        resp = self.client.get(
            reverse("pages:client-report"), {"download": report.pk}
        )

        self.assertIsNone(getattr(resp.wsgi_request, "live_update_interval", None))
        self.assertContains(resp, "report-download-frame")
        self.assertNotContains(resp, "setInterval(() => location.reload()")


class DeveloperArticleViewTests(TestCase):
    def test_published_article_renders(self):
        article = DeveloperArticle.objects.create(
            title="Protoline Integration",
            summary="Arthexis.com prepares for the 0.2 release.",
            content="## Release Overview\n\nExciting updates ahead.",
            is_published=True,
        )

        response = self.client.get(
            reverse("pages:developer-article", kwargs={"slug": article.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, article.title)
        self.assertContains(response, article.summary)
        self.assertContains(response, 'href="#release-overview"')
        self.assertContains(response, "markdown-body")

    def test_unpublished_article_returns_404(self):
        article = DeveloperArticle.objects.create(
            title="Hidden Draft",
            summary="Not yet ready.",
            content="Draft content.",
            is_published=False,
        )

        response = self.client.get(
            reverse("pages:developer-article", kwargs={"slug": article.slug})
        )

        self.assertEqual(response.status_code, 404)


class ScreenshotSpecInfrastructureTests(TestCase):
    def test_runner_creates_outputs_and_cleans_old_samples(self):
        spec = ScreenshotSpec(slug="spec-test", url="/")
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp)
            screenshot_path = temp_dir / "source.png"
            screenshot_path.write_bytes(b"fake")
            ContentSample.objects.create(
                kind=ContentSample.IMAGE,
                path="old.png",
                method="spec:old",
                hash="old-hash",
            )
            ContentSample.objects.filter(hash="old-hash").update(
                created_at=timezone.now() - timedelta(days=8)
            )
            with (
                patch(
                    "pages.screenshot_specs.base.capture_screenshot",
                    return_value=screenshot_path,
                ) as capture_mock,
                patch(
                    "pages.screenshot_specs.base.save_screenshot", return_value=None
                ) as save_mock,
            ):
                with ScreenshotSpecRunner(temp_dir) as runner:
                    result = runner.run(spec)
            self.assertTrue(result.image_path.exists())
            self.assertTrue(result.base64_path.exists())
            self.assertEqual(ContentSample.objects.filter(hash="old-hash").count(), 0)
            capture_mock.assert_called_once()
            save_mock.assert_called_once_with(screenshot_path, method="spec:spec-test")

    def test_runner_respects_manual_reason(self):
        spec = ScreenshotSpec(slug="manual-spec", url="/", manual_reason="hardware")
        with tempfile.TemporaryDirectory() as tmp:
            with ScreenshotSpecRunner(Path(tmp)) as runner:
                with self.assertRaises(ScreenshotUnavailable):
                    runner.run(spec)


class CaptureUIScreenshotsCommandTests(TestCase):
    def tearDown(self):
        registry.unregister("manual-cmd")
        registry.unregister("auto-cmd")

    def test_manual_spec_emits_warning(self):
        spec = ScreenshotSpec(slug="manual-cmd", url="/", manual_reason="manual")
        registry.register(spec)
        out = StringIO()
        call_command("capture_ui_screenshots", "--spec", spec.slug, stdout=out)
        self.assertIn("Skipping manual screenshot", out.getvalue())

    def test_command_invokes_runner(self):
        spec = ScreenshotSpec(slug="auto-cmd", url="/")
        registry.register(spec)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            image_path = tmp_path / "auto-cmd.png"
            base64_path = tmp_path / "auto-cmd.base64"
            image_path.write_bytes(b"fake")
            base64_path.write_text("Zg==", encoding="utf-8")
            runner = Mock()
            runner.__enter__ = Mock(return_value=runner)
            runner.__exit__ = Mock(return_value=None)
            runner.run.return_value = SimpleNamespace(
                image_path=image_path,
                base64_path=base64_path,
                sample=None,
            )
            with patch(
                "pages.management.commands.capture_ui_screenshots.ScreenshotSpecRunner",
                return_value=runner,
            ) as runner_cls:
                out = StringIO()
                call_command(
                    "capture_ui_screenshots",
                    "--spec",
                    spec.slug,
                    "--output-dir",
                    tmp_path,
                    stdout=out,
                )
            runner_cls.assert_called_once()
            runner.run.assert_called_once_with(spec)
            self.assertIn("Captured 'auto-cmd'", out.getvalue())


class ChatConsumerTests(TransactionTestCase):
    def _ensure_site(self):
        Site.objects.update_or_create(
            id=1, defaults={"domain": "example.com", "name": "Example"}
        )

    async def _open_session(self, message: str | None = None) -> str:
        from django.conf import settings

        assert getattr(settings, "PAGES_CHAT_ENABLED", False)
        session = SessionStore()
        await sync_to_async(session.save)()
        headers = [(b"cookie", f"sessionid={session.session_key}".encode("ascii"))]
        communicator = WebsocketCommunicator(
            application,
            "/ws/pages/chat/",
            headers=headers,
        )
        communicator.scope.setdefault("cookies", {})["sessionid"] = session.session_key
        communicator.scope["session"] = session
        connected, _ = await communicator.connect()
        assert connected

        history = None
        events = []
        for _ in range(5):
            try:
                event = await communicator.receive_json_from(timeout=3)
            except asyncio.TimeoutError:
                break
            events.append(event)
            if event.get("type") == "history":
                history = event
                break
        assert history is not None, f"Expected history event, saw: {events}"
        session_uuid = history.get("session")
        assert session_uuid

        if message:
            await communicator.send_json_to({"type": "message", "content": message})
            while True:
                payload = await communicator.receive_json_from()
                if payload.get("type") == "message":
                    break

        await communicator.disconnect()
        return session_uuid

    @override_settings(PAGES_CHAT_ENABLED=True)
    def test_chat_flow_persists_messages(self):
        self._ensure_site()
        session_uuid = async_to_sync(self._open_session)("Hello")
        stored = ChatMessage.objects.filter(body="Hello").first()
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(str(stored.session.uuid), session_uuid)

    @override_settings(PAGES_CHAT_ENABLED=True)
    def test_chat_rejects_unrelated_sessions(self):
        self._ensure_site()
        session_uuid = async_to_sync(self._open_session)()

        async def _attempt_foreign() -> bool:
            session = SessionStore()
            await sync_to_async(session.save)()
            headers = [(b"cookie", f"sessionid={session.session_key}".encode("ascii"))]
            communicator = WebsocketCommunicator(
                application,
                f"/ws/pages/chat/?session={session_uuid}",
                headers=headers,
            )
            communicator.scope.setdefault("cookies", {})["sessionid"] = (
                session.session_key
            )
            communicator.scope["session"] = session
            connected, _ = await communicator.connect()
            if connected:
                await communicator.disconnect()
            else:
                await communicator.wait()
            return connected

        connected = async_to_sync(_attempt_foreign)()
        self.assertFalse(connected)

    @override_settings(
        PAGES_CHAT_ENABLED=True,
        PAGES_CHAT_NOTIFY_STAFF=True,
        PAGES_CHAT_IDLE_ESCALATE_SECONDS=0,
    )
    def test_chat_idle_escalation_triggers_broadcast(self):
        self._ensure_site()
        with patch("nodes.models.NetMessage.broadcast") as mock_broadcast:
            async_to_sync(self._open_session)("Help")
        self.assertTrue(mock_broadcast.called)

    @override_settings(PAGES_CHAT_NOTIFY_STAFF=True)
    def test_chat_session_notify_staff_broadcasts_details(self):
        session = ChatSession()
        session.pk = 1
        session.created_at = timezone.now()
        session.last_activity_at = session.created_at
        message = ChatMessage(session=session, body="Need assistance", from_staff=False)
        with patch("nodes.models.NetMessage.broadcast") as mock_broadcast:
            result = session.notify_staff_of_message(message)
        self.assertTrue(result)
        call_args = mock_broadcast.call_args
        self.assertIsNotNone(call_args)
        subject = call_args.kwargs.get("subject") if call_args.kwargs else None
        if subject is None and call_args.args:
            subject = call_args.args[0]
        self.assertEqual(subject, "New visitor chat message")
        body = ""
        if call_args.kwargs and "body" in call_args.kwargs:
            body = call_args.kwargs["body"]
        elif call_args.args:
            body = call_args.args[1] if len(call_args.args) > 1 else ""
        self.assertIn(str(session.uuid), body)
        self.assertIn("Need assistance", body)
        self.assertIn("/admin/pages/chatsession/", body)

    @override_settings(PAGES_CHAT_NOTIFY_STAFF=True)
    def test_add_message_invokes_notification_for_visitors(self):
        session = ChatSession()
        session.pk = 1
        session.created_at = timezone.now()
        session.last_activity_at = session.created_at

        def _assign_pk(self, *args, **kwargs):
            self.pk = 1

        with patch.object(ChatMessage, "save", new=_assign_pk), patch.object(
            ChatSession, "save"
        ) as mock_session_save, patch.object(
            ChatSession, "notify_staff_of_message"
        ) as mock_notify, patch.object(
            ChatSession, "maybe_escalate_on_idle"
        ) as mock_escalate, patch(
            "pages.models.odoo_bridge.forward_chat_message"
        ) as mock_forward, patch(
            "pages.models.whatsapp_bridge.forward_chat_message"
        ) as mock_whatsapp_forward:
            mock_notify.return_value = True
            mock_escalate.return_value = False
            message = session.add_message(content="Need assistance", from_staff=False)
        mock_notify.assert_called_once()
        mock_escalate.assert_called_once()
        mock_session_save.assert_called()
        mock_forward.assert_called_once_with(session, message)
        mock_whatsapp_forward.assert_called_once_with(session, message)
        self.assertIsInstance(message, ChatMessage)

    @override_settings(PAGES_CHAT_NOTIFY_STAFF=True)
    def test_add_message_skips_notification_for_staff(self):
        session = ChatSession()
        session.pk = 1
        session.created_at = timezone.now()
        session.last_activity_at = session.created_at

        def _assign_pk(self, *args, **kwargs):
            self.pk = 1

        with patch.object(ChatMessage, "save", new=_assign_pk), patch.object(
            ChatSession, "save"
        ), patch.object(
            ChatSession, "notify_staff_of_message"
        ) as mock_notify, patch.object(
            ChatSession, "maybe_escalate_on_idle"
        ) as mock_escalate, patch(
            "pages.models.odoo_bridge.forward_chat_message"
        ) as mock_forward, patch(
            "pages.models.whatsapp_bridge.forward_chat_message"
        ) as mock_whatsapp_forward:
            mock_escalate.return_value = False
            session.add_message(content="Status update", from_staff=True)
        mock_notify.assert_not_called()
        mock_escalate.assert_not_called()
        mock_forward.assert_called_once()
        mock_whatsapp_forward.assert_called_once()

    @override_settings(PAGES_CHAT_NOTIFY_STAFF=True)
    def test_add_message_avoids_whatsapp_loopback(self):
        session = ChatSession(whatsapp_number="+15551234567")
        session.pk = 1
        session.created_at = timezone.now()
        session.last_activity_at = session.created_at

        def _assign_pk(self, *args, **kwargs):
            self.pk = 1

        with patch.object(ChatMessage, "save", new=_assign_pk), patch.object(
            ChatSession, "save"
        ), patch.object(
            ChatSession, "notify_staff_of_message"
        ), patch.object(
            ChatSession, "maybe_escalate_on_idle"
        ), patch(
            "pages.models.odoo_bridge.forward_chat_message"
        ) as mock_forward, patch(
            "pages.models.whatsapp_bridge.forward_chat_message"
        ) as mock_whatsapp_forward:
            session.add_message(
                content="Inbound from WhatsApp", source="whatsapp", from_staff=False
            )
        mock_forward.assert_called_once()
        mock_whatsapp_forward.assert_not_called()


class ChatWidgetViewTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(
            id=1, defaults={"domain": "example.com", "name": "Example"}
        )
        self.role, _ = NodeRole.objects.get_or_create(name="Interface")

    def _enable_chat_feature(self):
        node, _ = Node.objects.get_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "local",
                "address": "127.0.0.1",
                "port": 8000,
                "role": self.role,
            },
        )
        feature, _ = NodeFeature.objects.get_or_create(
            slug="chat-bridge", defaults={"display": "Chat Bridge"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)

    @override_settings(PAGES_CHAT_ENABLED=True)
    def test_chat_widget_present_when_enabled(self):
        self._enable_chat_feature()
        response = self.client.get(reverse("pages:index"))
        self.assertContains(response, 'id="chat-widget"')

    @override_settings(PAGES_CHAT_ENABLED=False)
    def test_chat_widget_hidden_when_disabled(self):
        self._enable_chat_feature()
        response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, 'id="chat-widget"')

    @override_settings(PAGES_CHAT_ENABLED=True)
    def test_chat_widget_hidden_when_feature_disabled(self):
        response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, 'id="chat-widget"')


class AdminChatWidgetTests(TestCase):
    @override_settings(PAGES_CHAT_ENABLED=True)
    def test_admin_chat_widget_renders_custom_controls(self):
        Site.objects.update_or_create(
            id=1, defaults={"domain": "example.com", "name": "Example"}
        )
        role, _ = NodeRole.objects.get_or_create(name="Interface")
        node, _ = Node.objects.get_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "local",
                "address": "127.0.0.1",
                "port": 8000,
                "role": role,
            },
        )
        feature, _ = NodeFeature.objects.get_or_create(
            slug="chat-bridge", defaults={"display": "Chat Bridge"}
        )
        NodeFeatureAssignment.objects.get_or_create(node=node, feature=feature)
        User = get_user_model()
        user = User.objects.create_superuser(
            username="chat-admin", email="chat@example.com", password="pwd"
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:index"))

        self.assertContains(response, "pages/js/chat.js")
        self.assertContains(response, "chat-close")
        self.assertContains(response, "data-chat-input")

    @override_settings(PAGES_CHAT_ENABLED=True)
    def test_admin_chat_widget_hidden_without_feature(self):
        Site.objects.update_or_create(
            id=1, defaults={"domain": "example.com", "name": "Example"}
        )
        User = get_user_model()
        user = User.objects.create_superuser(
            username="chat-admin", email="chat@example.com", password="pwd"
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:index"))

        self.assertNotContains(response, "pages/js/chat.js")
        self.assertNotContains(response, 'id="chat-widget"')


class OdooChatBridgeTests(TestCase):
    def setUp(self):
        self.site = Site(domain="bridge.example.com", name="Bridge")
        User = get_user_model()
        self.user = User.objects.create_user(username="bridge-user", password="pwd")
        self.profile = OdooProfile.objects.create(
            user=self.user,
            host="https://odoo.example.com",
            database="example",
            username="demo",
            password="secret",
            partner_id=42,
            odoo_uid=7,
            verified_on=timezone.now(),
        )

    def test_post_message_calls_odoo(self):
        bridge = OdooChatBridge.objects.create(
            site=None,
            profile=self.profile,
            channel_id=77,
        )
        session = ChatSession(site=None)
        message = ChatMessage(session=session, body="Need help", from_staff=False)
        with patch.object(OdooProfile, "execute", return_value=None) as mock_execute:
            result = bridge.post_message(session, message)
        self.assertTrue(result)
        mock_execute.assert_called_once()
        args, kwargs = mock_execute.call_args
        self.assertEqual(args[0], "mail.channel")
        self.assertEqual(args[1], "message_post")
        self.assertEqual(args[2], [bridge.channel_id])
        payload = args[3]
        self.assertIn("body", payload)
        self.assertIn("Need help", payload["body"])
        partner_ids = payload.get("partner_ids", [])
        self.assertIn(self.profile.partner_id, partner_ids)

    def test_forward_chat_message_uses_site_bridge(self):
        bridge = OdooChatBridge.objects.create(
            site=None,
            profile=self.profile,
            channel_id=88,
        )
        session = ChatSession(site=self.site)
        message = ChatMessage(session=session, body="Hello", from_staff=False)
        with patch(
            "pages.odoo.OdooChatBridge.objects.for_site", return_value=bridge
        ) as mock_for_site, patch.object(
            OdooChatBridge, "post_message", return_value=True
        ) as mock_post:
            result = forward_chat_message(session, message)
        self.assertTrue(result)
        mock_for_site.assert_called_once_with(self.site)
        mock_post.assert_called_once_with(session, message)

    def test_forward_chat_message_returns_false_without_bridge(self):
        session = ChatSession(site=None)
        message = ChatMessage(session=session, body="Hello", from_staff=False)
        result = forward_chat_message(session, message)
        self.assertFalse(result)


class WhatsAppChatBridgeTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(domain="whatsapp.example.com", name="WA")
        self.bridge = WhatsAppChatBridge.objects.create(
            site=None,
            phone_number_id="1111",
            access_token="token",
            is_default=True,
        )

    @override_settings(PAGES_WHATSAPP_ENABLED=True)
    def test_send_message_posts_payload(self):
        session = ChatSession(site=self.site, whatsapp_number="+15551234567")
        message = ChatMessage(session=session, body="Hello", from_staff=True)
        fake_response = SimpleNamespace(status_code=200, text="")
        with patch("pages.models.requests.post", return_value=fake_response) as mock_post:
            result = self.bridge.send_message(
                recipient=session.whatsapp_number,
                content=message.body,
                session=session,
                message=message,
            )
        self.assertTrue(result)
        mock_post.assert_called_once()
        called_url = mock_post.call_args.args[0]
        self.assertIn(self.bridge.phone_number_id, called_url)
        payload = mock_post.call_args.kwargs.get("json", {})
        self.assertEqual(payload.get("text", {}).get("body"), "Hello")

    @override_settings(PAGES_WHATSAPP_ENABLED=True)
    def test_send_message_handles_http_errors(self):
        session = ChatSession(site=self.site, whatsapp_number="+15557654321")
        message = ChatMessage(session=session, body="Hello", from_staff=True)
        fake_response = SimpleNamespace(status_code=500, text="error")
        with patch("pages.models.requests.post", return_value=fake_response):
            result = self.bridge.send_message(
                recipient=session.whatsapp_number,
                content=message.body,
                session=session,
                message=message,
            )
        self.assertFalse(result)

    @override_settings(PAGES_WHATSAPP_ENABLED=True)
    def test_forward_chat_message_uses_site_bridge(self):
        session = ChatSession(site=self.site, whatsapp_number="+15550001111")
        message = ChatMessage(session=session, body="Ping", from_staff=True)
        with patch(
            "pages.whatsapp.WhatsAppChatBridge.objects.for_site",
            return_value=self.bridge,
        ) as mock_for_site, patch.object(
            WhatsAppChatBridge, "send_message", return_value=True
        ) as mock_send:
            result = forward_whatsapp_message(session, message)
        self.assertTrue(result)
        mock_for_site.assert_called_once_with(self.site)
        mock_send.assert_called_once_with(
            recipient=session.whatsapp_number,
            content=message.body,
            session=session,
            message=message,
        )

    @override_settings(PAGES_WHATSAPP_ENABLED=False)
    def test_forward_chat_message_respects_disabled_setting(self):
        session = ChatSession(site=self.site, whatsapp_number="+15550002222")
        message = ChatMessage(session=session, body="Ping", from_staff=True)
        self.assertFalse(forward_whatsapp_message(session, message))


class WhatsAppWebhookTests(TestCase):
    def setUp(self):
        self.url = reverse("pages:whatsapp-webhook")
        self.site = Site.objects.create(domain="hook.example.com", name="Hook")

    @override_settings(PAGES_WHATSAPP_ENABLED=True)
    def test_webhook_creates_session_and_message(self):
        payload = {"from": "+14445556666", "message": "Need help", "site": self.site.id}
        response = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        session = ChatSession.objects.filter(whatsapp_number=payload["from"]).first()
        assert session is not None
        self.assertEqual(session.site, self.site)
        self.assertEqual(session.messages.count(), 1)
        self.assertEqual(session.messages.first().body, payload["message"])

    @override_settings(PAGES_WHATSAPP_ENABLED=True)
    def test_webhook_rejects_invalid_payload(self):
        response = self.client.post(
            self.url, data="not-json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(PAGES_WHATSAPP_ENABLED=False)
    def test_webhook_respects_disabled_setting(self):
        payload = {"from": "+19998887777", "message": "Hello"}
        response = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 503)
