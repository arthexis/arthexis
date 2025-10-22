import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
import pytest

django.setup()

from django.test import Client, RequestFactory, TestCase, SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.templatetags.static import static
from urllib.parse import quote
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.contrib import admin, messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import DisallowedHost
from django.core.cache import cache
from django.db import connection
import socket
from django.db import connection
from pages import site_config
from pages.models import (
    Application,
    Landing,
    Module,
    RoleLanding,
    SiteBadge,
    Favorite,
    ViewHistory,
    LandingLead,
    UserManual,
    UserStory,
)
from django.http import FileResponse

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
from django.apps import apps as django_apps
from config.middleware import SiteHttpsRedirectMiddleware
from core import mailer
from core.admin import ProfileAdminMixin
from core.models import (
    AdminHistory,
    InviteLead,
    Package,
    Reference,
    RFID,
    ReleaseManager,
    SecurityGroup,
    Todo,
    TOTPDeviceSettings,
)
from ocpp.models import Charger
from django.core.files.uploadedfile import SimpleUploadedFile
import base64
import json
import tempfile
import shutil
from io import StringIO
from django.conf import settings
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch
from types import SimpleNamespace
from django.core.management import call_command
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
from django.utils.translation import gettext
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from core.backends import TOTP_DEVICE_NAME
import time

from nodes.models import (
    EmailOutbox,
    Node,
    ContentSample,
    NodeRole,
    NodeFeature,
    NodeFeatureAssignment,
)
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

    def test_login_link_in_navbar(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'href="/login/"')

    def test_login_page_shows_authenticator_toggle(self):
        resp = self.client.get(reverse("pages:login"))
        self.assertContains(resp, "Use Authenticator app")

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
        device = TOTPDevice.objects.create(
            user=self.staff,
            name=TOTP_DEVICE_NAME,
            confirmed=True,
        )
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        totp.time = time.time()
        token = f"{totp.token():0{device.digits}d}"

        resp = self.client.post(
            reverse("pages:login"),
            {
                "username": "staff",
                "auth_method": "otp",
                "otp_token": token,
            },
        )

        self.assertRedirects(resp, reverse("admin:index"))
        session = self.client.session
        self.assertIn(DEVICE_ID_SESSION_KEY, session)
        self.assertEqual(session[DEVICE_ID_SESSION_KEY], device.persistent_id)

    def test_login_with_invalid_authenticator_code(self):
        TOTPDevice.objects.create(
            user=self.staff,
            name=TOTP_DEVICE_NAME,
            confirmed=True,
        )

        resp = self.client.post(
            reverse("pages:login"),
            {
                "username": "staff",
                "auth_method": "otp",
                "otp_token": "000000",
            },
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "authenticator code is invalid", status_code=200)

    def test_already_logged_in_staff_redirects(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("pages:login"))
        self.assertRedirects(resp, reverse("admin:index"))

    def test_regular_user_redirects_next(self):
        resp = self.client.post(
            reverse("pages:login") + "?next=/nodes/list/",
            {"username": "user", "password": "pwd"},
        )
        self.assertRedirects(resp, "/nodes/list/")

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
                "port": 8000,
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
        self.assertNotContains(resp, "5. Horologia MODELS")

    def test_horologia_visible_with_celery_feature(self):
        feature = NodeFeature.objects.create(slug="celery-queue", display="Celery Queue")
        NodeFeatureAssignment.objects.create(node=self.node, feature=feature)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "5. Horologia MODELS")

    def test_horologia_visible_with_celery_lock(self):
        self.celery_lock.write_text("")
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "5. Horologia MODELS")

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


class ViewHistoryLoggingTests(TestCase):
    def setUp(self):
        self.client = Client()
        Site.objects.update_or_create(id=1, defaults={"name": "Terminal"})
        self.addCleanup(self._reset_purge_task)

    def _reset_purge_task(self):
        from django_celery_beat.models import PeriodicTask

        PeriodicTask.objects.filter(name="pages_purge_landing_leads").delete()

    def _create_local_node(self):
        node, _ = Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": socket.gethostname(),
                "address": "127.0.0.1",
                "base_path": settings.BASE_DIR,
                "port": 8000,
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
                "port": 8000,
            },
        )
        self.node.features.clear()
        self.addCleanup(self._reset_purge_task)

    def _reset_purge_task(self):
        from django_celery_beat.models import PeriodicTask

        PeriodicTask.objects.filter(name="pages_purge_landing_leads").delete()

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

    @patch("pages.templatetags.admin_extras.connection.introspection.table_names")
    def test_status_dots_render(self, mock_tables):
        from django.db import connection

        tables = type(connection.introspection).table_names(connection.introspection)
        mock_tables.return_value = [t for t in tables if t != "pages_module"]
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, 'class="model-status ok"')
        self.assertContains(resp, 'class="model-status missing"', count=1)


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
        resp = self.client.get(reverse("pages:index"), HTTP_HOST="127.0.0.1:8000")
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
            defaults={"path": "/ocpp/", "menu": "Chargers"},
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
            "Control", "/ocpp/rfid/validator/", "RFID Tag Validator"
        )
        resp = self.client.get(reverse("pages:index"))
        self.assertRedirects(resp, target, fetch_redirect_response=False)

    def test_security_group_redirect_takes_priority(self):
        self._configure_role_landing(
            "Control", "/ocpp/rfid/validator/", "RFID Tag Validator"
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
            "Control", "/ocpp/rfid/validator/", "RFID Tag Validator"
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
        self.assertIn("RFID Tag Validator", landing_labels)

    @override_settings(ALLOWED_HOSTS=["testserver", "arthexis.com"])
    def test_cookbooks_pill_visible_for_arthexis(self):
        resp = self.client.get(
            reverse("pages:index"), HTTP_HOST="arthexis.com"
        )
        self.assertContains(resp, 'badge rounded-pill text-bg-secondary">COOKBOOKS')

    def test_ocpp_dashboard_visible(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'href="/ocpp/cpms/dashboard/"')


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
        application, _ = Application.objects.get_or_create(name="core")
        module, _ = Module.objects.get_or_create(
            node_role=role,
            application=application,
            path="/release/",
            defaults={"menu": "Release", "is_default": False},
        )
        module_updates = []
        if module.menu != "Release":
            module.menu = "Release"
            module_updates.append("menu")
        if getattr(module, "is_deleted", False):
            module.is_deleted = False
            module_updates.append("is_deleted")
        if module_updates:
            module.save(update_fields=module_updates)
        Landing.objects.update_or_create(
            module=module,
            path="/release/",
            defaults={
                "label": "Package Releases",
                "enabled": True,
                "description": "",
            },
        )
        self.release_group, _ = SecurityGroup.objects.get_or_create(
            name="Release Managers"
        )

    def test_release_module_hidden_for_anonymous(self):
        response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, 'badge rounded-pill text-bg-secondary">RELEASE')

    def test_release_module_visible_to_release_manager(self):
        user = self.user_model.objects.create_user(
            "release-admin", password="test", is_staff=True
        )
        user.groups.add(self.release_group)
        self.client.force_login(user)
        response = self.client.get(reverse("pages:index"))
        self.assertContains(response, 'badge rounded-pill text-bg-secondary">RELEASE')

    def test_release_module_hidden_for_non_member_staff(self):
        user = self.user_model.objects.create_user(
            "staff-user", password="test", is_staff=True
        )
        self.client.force_login(user)
        response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, 'badge rounded-pill text-bg-secondary">RELEASE')


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
        self.assertContains(resp, "LINKS")
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

    def test_readme_pill_visible(self):
        resp = self.client.get(reverse("pages:readme"))
        self.assertContains(resp, 'href="/read/"')
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
            '<a class="nav-link" href="/read/"><span class="badge rounded-pill text-bg-secondary">COOKBOOKS</span></a>',
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
        self.assertContains(resp, 'href="/read/"')
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

        self.assertEqual(chargers.menu, "Chargers")
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
            {"/awg/", "/awg/energy-tariff/"},
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

    def test_cancel_link_uses_next(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        next_url = reverse("admin:pages_application_changelist")
        url = (
            reverse("admin:favorite_toggle", args=[ct.id]) + f"?next={quote(next_url)}"
        )
        resp = self.client.get(url)
        self.assertContains(resp, f'href="{next_url}"')

    def test_existing_favorite_redirects_to_list(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        Favorite.objects.create(user=self.user, content_type=ct)
        url = reverse("admin:favorite_toggle", args=[ct.id])
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse("admin:favorite_list"))
        resp = self.client.get(reverse("admin:favorite_list"))
        self.assertContains(resp, ct.name)

    def test_update_user_data_from_list(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        fav = Favorite.objects.create(user=self.user, content_type=ct)
        url = reverse("admin:favorite_list")
        resp = self.client.post(url, {"user_data": [str(fav.pk)]})
        self.assertRedirects(resp, url)
        fav.refresh_from_db()
        self.assertTrue(fav.user_data)

    def test_dashboard_includes_favorites_and_user_data(self):
        fav_ct = ContentType.objects.get_by_natural_key("pages", "application")
        Favorite.objects.create(
            user=self.user, content_type=fav_ct, custom_label="Apps"
        )
        NodeRole.objects.create(name="DataRole", is_user_data=True)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, reverse("admin:pages_application_changelist"))
        self.assertContains(resp, reverse("admin:nodes_noderole_changelist"))

    def test_dashboard_merges_duplicate_future_actions(self):
        ct = ContentType.objects.get_for_model(NodeRole)
        Favorite.objects.create(user=self.user, content_type=ct)
        NodeRole.objects.create(name="DataRole2", is_user_data=True)
        AdminHistory.objects.create(
            user=self.user,
            content_type=ct,
            url=reverse("admin:nodes_noderole_changelist"),
        )
        resp = self.client.get(reverse("admin:index"))
        url = reverse("admin:nodes_noderole_changelist")
        self.assertGreaterEqual(resp.content.decode().count(url), 1)
        self.assertContains(resp, NodeRole._meta.verbose_name_plural)

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

        self.assertIn('class="lead-open-badge"', content)
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
            "%(available)s chargers reporting Available status with a CP number, out of %(total)s total Available chargers. %(missing)s Available chargers are missing a connector number."
        ) % {"available": 1, "total": 2, "missing": 1}

        self.assertContains(resp, expected)
        self.assertContains(resp, 'class="charger-availability-badge"')
        self.assertContains(resp, f'title="{badge_label}"')
        self.assertContains(resp, f'aria-label="{badge_label}"')

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
        self.assertContains(resp, 'class="charger-availability-badge"')
        self.assertContains(resp, f'title="{badge_label}"')
        self.assertContains(resp, f'aria-label="{badge_label}"')

    def test_nav_sidebar_hides_dashboard_badges(self):
        InviteLead.objects.create(email="open@example.com")
        RFID.objects.create(rfid="RFID0003", released=True, allowed=True)

        resp = self.client.get(reverse("admin:teams_invitelead_changelist"))

        self.assertNotContains(resp, "lead-open-badge")
        self.assertNotContains(resp, "rfid-release-badge")

    def test_dashboard_limits_future_actions_to_top_four(self):
        from pages.templatetags.admin_extras import future_action_items

        role_ct = ContentType.objects.get_for_model(NodeRole)
        role_url = reverse("admin:nodes_noderole_changelist")
        AdminHistory.objects.create(
            user=self.user,
            content_type=role_ct,
            url=role_url,
        )
        AdminHistory.objects.create(
            user=self.user,
            content_type=role_ct,
            url=f"{role_url}?page=2",
        )
        AdminHistory.objects.create(
            user=self.user,
            content_type=role_ct,
            url=f"{role_url}?page=3",
        )

        app_ct = ContentType.objects.get_for_model(Application)
        app_url = reverse("admin:pages_application_changelist")
        AdminHistory.objects.create(
            user=self.user,
            content_type=app_ct,
            url=app_url,
        )
        AdminHistory.objects.create(
            user=self.user,
            content_type=app_ct,
            url=f"{app_url}?page=2",
        )

        module_ct = ContentType.objects.get_for_model(Module)
        module_url = reverse("admin:pages_module_changelist")
        AdminHistory.objects.create(
            user=self.user,
            content_type=module_ct,
            url=module_url,
        )
        AdminHistory.objects.create(
            user=self.user,
            content_type=module_ct,
            url=f"{module_url}?page=2",
        )

        package_ct = ContentType.objects.get_for_model(Package)
        package_url = reverse("admin:core_package_changelist")
        AdminHistory.objects.create(
            user=self.user,
            content_type=package_ct,
            url=package_url,
        )

        view_history_ct = ContentType.objects.get_for_model(ViewHistory)
        view_history_url = reverse("admin:pages_viewhistory_changelist")
        AdminHistory.objects.create(
            user=self.user,
            content_type=view_history_ct,
            url=view_history_url,
        )

        resp = self.client.get(reverse("admin:index"))
        items = future_action_items({"request": resp.wsgi_request})["models"]
        labels = {item["label"] for item in items}
        self.assertEqual(len(items), 4)
        self.assertIn("Node Roles", labels)
        self.assertIn("Modules", labels)
        self.assertIn("applications", labels)
        self.assertIn("View Histories", labels)
        self.assertNotIn("Packages", labels)
        ContentType.objects.clear_cache()

    def test_future_action_items_limits_user_data_queries(self):
        from pages.templatetags import admin_extras

        cache.delete(admin_extras.USER_DATA_MODELS_CACHE_KEY)
        self.addCleanup(cache.delete, admin_extras.USER_DATA_MODELS_CACHE_KEY)

        for index in range(3):
            NodeRole.objects.create(name=f"CachedRole{index}", is_user_data=True)
        for index in range(2):
            NodeFeature.objects.create(
                slug=f"cached-feature-{index}",
                display=f"Feature {index}",
                is_user_data=True,
            )

        Node.objects.create(
            hostname="cached-node",
            address="127.0.0.1",
            mac_address="AA:BB:CC:DD:EE:FF",
            port=8000,
            is_user_data=True,
        )

        response = self.client.get(reverse("admin:index"))
        request = response.wsgi_request

        admin_extras.future_action_items({"request": request})
        with CaptureQueriesContext(connection) as ctx:
            admin_extras.future_action_items({"request": request})

        # History and favorites queries should remain bounded regardless of the
        # number of Entity subclasses with user data.
        self.assertLessEqual(len(ctx.captured_queries), 4)

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

    def test_dashboard_links_to_focus_view(self):
        todo = Todo.objects.create(request="Check docs", url="/docs/")
        resp = self.client.get(reverse("admin:index"))
        focus_url = reverse("todo-focus", args=[todo.pk])
        expected_next = quote(reverse("admin:index"))
        self.assertContains(
            resp,
            f'href="{focus_url}?next={expected_next}"',
        )

    def test_dashboard_shows_todo_with_done_button(self):
        todo = Todo.objects.create(request="Do thing")
        resp = self.client.get(reverse("admin:index"))
        done_url = reverse("todo-done", args=[todo.pk])
        self.assertContains(resp, todo.request)
        self.assertContains(resp, f'action="{done_url}"')
        self.assertContains(resp, "DONE")

    def test_dashboard_shows_request_details(self):
        Todo.objects.create(request="Do thing", request_details="More info")
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(
            resp, '<div class="todo-details">More info</div>', html=True
        )

    def test_dashboard_hides_completed_todos(self):
        todo = Todo.objects.create(request="Completed task")
        Todo.objects.filter(pk=todo.pk).update(done_on=timezone.now())

        resp = self.client.get(reverse("admin:index"))

        self.assertNotContains(resp, todo.request)
        self.assertNotContains(resp, "Completed")

    def test_dashboard_shows_todos_when_node_unknown(self):
        Todo.objects.create(request="Check fallback")
        from nodes.models import Node

        Node.objects.all().delete()

        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Release manager tasks")
        self.assertContains(resp, "Check fallback")

    def test_dashboard_shows_todos_without_release_manager_profile(self):
        Todo.objects.create(request="Unrestricted task")
        ReleaseManager.objects.filter(user=self.user).delete()

        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Release manager tasks")
        self.assertContains(resp, "Unrestricted task")

    def test_dashboard_excludes_todo_changelist_link(self):
        ct = ContentType.objects.get_for_model(Todo)
        Favorite.objects.create(user=self.user, content_type=ct)
        AdminHistory.objects.create(
            user=self.user,
            content_type=ct,
            url=reverse("admin:core_todo_changelist"),
        )
        Todo.objects.create(request="Task", is_user_data=True)
        resp = self.client.get(reverse("admin:index"))
        changelist = reverse("admin:core_todo_changelist")
        self.assertNotContains(resp, f'href="{changelist}"')

    def test_dashboard_shows_todos_for_admin_without_release_manager(self):
        todo = Todo.objects.create(request="Only Release Manager")
        User = get_user_model()
        other_user = User.objects.create_superuser(
            username="norole", password="pwd", email="norole@example.com"
        )
        self.client.force_login(other_user)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Release manager tasks")
        self.assertContains(resp, todo.request)

    def test_dashboard_shows_todos_for_non_terminal_node(self):
        todo = Todo.objects.create(request="Terminal Tasks")
        from nodes.models import NodeRole

        control_role, _ = NodeRole.objects.get_or_create(name="Control")
        self.node.role = control_role
        self.node.save(update_fields=["role"])
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Release manager tasks")
        self.assertContains(resp, todo.request)

    def test_dashboard_shows_todos_for_delegate_release_manager(self):
        todo = Todo.objects.create(request="Delegate Task")
        User = get_user_model()
        delegate = User.objects.create_superuser(
            username="delegate",
            password="pwd",
            email="delegate@example.com",
        )
        ReleaseManager.objects.create(user=delegate)
        operator = User.objects.create_superuser(
            username="operator",
            password="pwd",
            email="operator@example.com",
        )
        operator.operate_as = delegate
        operator.full_clean()
        operator.save()
        self.client.force_login(operator)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Release manager tasks")
        self.assertContains(resp, todo.request)


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


class DatasetteTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username="ds", password="pwd")
        Site.objects.update_or_create(id=1, defaults={"name": "Terminal"})

    def test_datasette_auth_endpoint(self):
        resp = self.client.get(reverse("pages:datasette-auth"))
        self.assertEqual(resp.status_code, 401)
        self.client.force_login(self.user)
        resp = self.client.get(reverse("pages:datasette-auth"))
        self.assertEqual(resp.status_code, 200)

    def test_navbar_includes_datasette_when_enabled(self):
        lock_dir = Path(settings.BASE_DIR) / "locks"
        lock_dir.mkdir(exist_ok=True)
        lock_file = lock_dir / "datasette.lck"
        try:
            lock_file.touch()
            resp = self.client.get(reverse("pages:index"))
            self.assertContains(resp, 'href="/data/"')
        finally:
            lock_file.unlink(missing_ok=True)

    def test_admin_home_includes_datasette_button_when_enabled(self):
        lock_dir = Path(settings.BASE_DIR) / "locks"
        lock_dir.mkdir(exist_ok=True)
        lock_file = lock_dir / "datasette.lck"
        try:
            lock_file.touch()
            self.user.is_staff = True
            self.user.is_superuser = True
            self.user.save()
            self.client.force_login(self.user)
            resp = self.client.get(reverse("admin:index"))
            self.assertContains(resp, 'href="/data/"')
            self.assertContains(resp, ">Datasette<")
        finally:
            lock_file.unlink(missing_ok=True)


class UserStorySubmissionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse("pages:user-story-submit")
        User = get_user_model()
        self.user = User.objects.create_user(username="feedbacker", password="pwd")

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

    def test_anonymous_submission_uses_provided_name(self):
        response = self.client.post(
            self.url,
            {
                "name": "Guest Reviewer",
                "rating": 3,
                "comments": "It was fine.",
                "path": "/status/",
                "take_screenshot": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserStory.objects.count(), 1)
        story = UserStory.objects.get()
        self.assertEqual(story.name, "Guest Reviewer")
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

    def test_anonymous_submission_without_name_uses_fallback(self):
        response = self.client.post(
            self.url,
            {
                "rating": 2,
                "comments": "Could be better.",
                "path": "/feedback/",
                "take_screenshot": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        story = UserStory.objects.get()
        self.assertEqual(story.name, "Anonymous")
        self.assertIsNone(story.user)
        self.assertIsNone(story.owner)
        self.assertTrue(story.take_screenshot)
        self.assertEqual(story.status, UserStory.Status.OPEN)

    def test_submission_without_screenshot_request(self):
        response = self.client.post(
            self.url,
            {
                "rating": 4,
                "comments": "Skip the screenshot, please.",
                "path": "/feedback/",
            },
        )
        self.assertEqual(response.status_code, 200)
        story = UserStory.objects.get()
        self.assertFalse(story.take_screenshot)
        self.assertIsNone(story.owner)

    def test_rate_limit_blocks_repeated_submissions(self):
        payload = {
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
        self.assertEqual(resp.context["request"].live_update_interval, 5)
        self.assertContains(resp, "setInterval(() => location.reload()")


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
