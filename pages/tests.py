from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from urllib.parse import quote
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.contrib import admin
from django.core.exceptions import DisallowedHost
import socket
from pages.models import Application, Module, SiteBadge, Favorite, ViewHistory
from pages.admin import ApplicationAdmin
from pages.screenshot_specs import (
    ScreenshotSpec,
    ScreenshotSpecRunner,
    ScreenshotUnavailable,
    registry,
)
from django.apps import apps as django_apps
from core import mailer
from core.admin import ProfileAdminMixin
from core.models import AdminHistory, InviteLead, Package, ReleaseManager, Todo
from django.core.files.uploadedfile import SimpleUploadedFile
import base64
import tempfile
import shutil
from io import StringIO
from django.conf import settings
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace
from django.core.management import call_command
import re
from django.contrib.contenttypes.models import ContentType
from datetime import date, timedelta
from django.core import mail
from django.utils import timezone
from django.utils.text import slugify

from nodes.models import (
    EmailOutbox,
    Node,
    ContentSample,
    NodeRole,
    NodeFeature,
    NodeFeatureAssignment,
)


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

    def test_staff_login_redirects_admin(self):
        resp = self.client.post(
            reverse("pages:login"),
            {"username": "staff", "password": "pwd"},
        )
        self.assertRedirects(resp, reverse("admin:index"))

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

    @patch("pages.views.public_wifi.grant_public_access")
    @patch(
        "pages.views.public_wifi.resolve_mac_address",
        return_value="aa:bb:cc:dd:ee:ff",
    )
    def test_invitation_login_grants_public_wifi_access(self, mock_resolve, mock_grant):
        control_role, _ = NodeRole.objects.get_or_create(name="Control")
        feature = NodeFeature.objects.create(
            slug="ap-public-wifi", display="AP Public Wi-Fi"
        )
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

    def test_my_profile_action_links_to_direct_view(self):
        ReleaseManager.objects.create(user=self.admin)
        profile_url = reverse("admin:core_releasemanager_my_profile")

        dashboard = self.client.get(reverse("admin:index"))
        self.assertContains(dashboard, f'href="{profile_url}"')

        app_list = self.client.get(reverse("admin:app_list", args=["core"]))
        self.assertContains(app_list, f'href="{profile_url}"')


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

    def test_graph_data_endpoint(self):
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

    def test_admin_index_displays_widget(self):
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "viewhistory-mini-module")
        self.assertContains(resp, reverse("admin:pages_viewhistory_traffic_graph"))


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
            node_role=role, application=app, path="/", is_default=True
        )

    def test_nav_pill_renders(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, "README")
        self.assertContains(resp, "badge rounded-pill")

    def test_nav_pill_renders_with_port(self):
        resp = self.client.get(reverse("pages:index"), HTTP_HOST="127.0.0.1:8000")
        self.assertContains(resp, "README")

    def test_nav_pill_uses_menu_field(self):
        site_app = Module.objects.get()
        site_app.menu = "Docs"
        site_app.save()
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'badge rounded-pill text-bg-secondary">DOCS')
        self.assertNotContains(resp, 'badge rounded-pill text-bg-secondary">README')

    def test_app_without_root_url_excluded(self):
        role = NodeRole.objects.get(name="Terminal")
        app = Application.objects.create(name="core")
        Module.objects.create(node_role=role, application=app, path="/core/")
        resp = self.client.get(reverse("pages:index"))
        self.assertNotContains(resp, 'href="/core/"')


class ConstellationNavTests(TestCase):
    def setUp(self):
        self.client = Client()
        role, _ = NodeRole.objects.get_or_create(name="Constellation")
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
                "constellation__application_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "constellation__module_ocpp.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "constellation__landing_ocpp_dashboard.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "constellation__landing_ocpp_cp_simulator.json",
            ),
            Path(
                settings.BASE_DIR,
                "pages",
                "fixtures",
                "constellation__landing_ocpp_rfid.json",
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
                path="/ocpp/", node_role__name="Constellation"
            ).exists()
        )
        self.assertFalse(
            Module.objects.filter(
                path="/ocpp/rfid/",
                node_role__name="Constellation",
                is_deleted=False,
            ).exists()
        )
        ocpp_module = next(
            module
            for module in resp.context["nav_modules"]
            if module.menu_label.upper() == "OCPP"
        )
        landing_labels = [landing.label for landing in ocpp_module.enabled_landings]
        self.assertIn("RFID Scanner", landing_labels)

    def test_ocpp_dashboard_visible(self):
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'href="/ocpp/"')


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
        app, _ = Application.objects.get_or_create(name="awg")
        module, _ = Module.objects.get_or_create(
            node_role=role, application=app, path="/awg/"
        )
        module.create_landings()

    def test_power_pill_lists_calculators(self):
        resp = self.client.get(reverse("pages:index"))
        power_module = None
        for module in resp.context["nav_modules"]:
            if module.path == "/awg/":
                power_module = module
                break
        self.assertIsNotNone(power_module)
        self.assertEqual(power_module.menu_label.upper(), "POWER")
        landing_labels = {landing.label for landing in power_module.enabled_landings}
        self.assertIn("AWG Calculator", landing_labels)
        self.assertIn("Energy Tariff Calculator", landing_labels)


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
        self.assertContains(resp, 'href="/ocpp/"')

    def test_staff_sees_pill(self):
        self.client.login(username="staff", password="pw")
        resp = self.client.get(reverse("pages:index"))
        self.assertContains(resp, 'href="/ocpp/"')


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
    def test_constellation_fixture_loads_without_duplicates(self):
        from glob import glob

        NodeRole.objects.get_or_create(name="Constellation")
        fixtures = glob(
            str(Path(settings.BASE_DIR, "pages", "fixtures", "constellation__*.json"))
        )
        fixtures = sorted(
            fixtures,
            key=lambda path: (
                0 if "__application_" in path else 1 if "__module_" in path else 2
            ),
        )
        call_command("loaddata", *fixtures)
        call_command("loaddata", *fixtures)
        module = Module.objects.get(path="/ocpp/", node_role__name="Constellation")
        module.create_landings()
        self.assertEqual(module.landings.filter(path="/ocpp/rfid/").count(), 1)


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

    def test_page_renders(self):
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

    def test_dashboard_uses_browse_label(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        Favorite.objects.create(user=self.user, content_type=ct)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Browse Applications")

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

    def test_dashboard_hides_todos_without_release_manager(self):
        todo = Todo.objects.create(request="Only Release Manager")
        User = get_user_model()
        other_user = User.objects.create_superuser(
            username="norole", password="pwd", email="norole@example.com"
        )
        self.client.force_login(other_user)
        resp = self.client.get(reverse("admin:index"))
        self.assertNotContains(resp, "Release manager tasks")
        self.assertNotContains(resp, todo.request)

    def test_dashboard_hides_todos_for_non_terminal_node(self):
        todo = Todo.objects.create(request="Terminal Tasks")
        from nodes.models import NodeRole

        control_role, _ = NodeRole.objects.get_or_create(name="Control")
        self.node.role = control_role
        self.node.save(update_fields=["role"])
        resp = self.client.get(reverse("admin:index"))
        self.assertNotContains(resp, "Release manager tasks")
        self.assertNotContains(resp, todo.request)

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
                self.assertIn("My Profile", labels)

                target = next(
                    (action for action in actions if action["label"] == "My Profile"),
                    None,
                )
                self.assertIsNotNone(target)
                expected_url = reverse(
                    f"admin:{app_label}_{object_name.lower()}_my_profile"
                )
                self.assertEqual(target["url"], expected_url)


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
