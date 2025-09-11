from django.test import Client, TestCase, override_settings
from django.urls import reverse
from urllib.parse import quote
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.contrib import admin
from django.core.exceptions import DisallowedHost
import socket
from pages.models import Application, Module, SiteBadge, Favorite
from core.user_data import UserDatum
from pages.admin import ApplicationAdmin
from django.apps import apps as django_apps
from core.models import AdminHistory, InviteLead, Todo, NewsArticle
from django.core.files.uploadedfile import SimpleUploadedFile
import base64
import tempfile
import shutil
from django.conf import settings
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace
from django.core import mail
from django.core.management import call_command
import re
from django.contrib.contenttypes.models import ContentType
from datetime import date

from nodes.models import Node, ContentSample, NodeRole


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
        with patch("pages.views.send_mail", side_effect=Exception("fail")):
            resp = self.client.post(
                reverse("pages:request-invite"), {"email": "invite@example.com"}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "If the email exists, an invitation has been sent.")
        lead = InviteLead.objects.get()
        self.assertIsNone(lead.sent_on)
        self.assertIn("fail", lead.error)
        self.assertIn("email service", lead.error)

    def test_request_invite_records_send_time(self):
        resp = self.client.post(
            reverse("pages:request-invite"), {"email": "invite@example.com"}
        )
        self.assertEqual(resp.status_code, 200)
        lead = InviteLead.objects.get()
        self.assertIsNotNone(lead.sent_on)
        self.assertEqual(lead.error, "")

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

    def test_request_invite_falls_back_to_send_mail(self):
        node = Node.objects.create(
            hostname="local", address="127.0.0.1", mac_address="00:11:22:33:44:55"
        )
        with (
            patch("pages.views.Node.get_local", return_value=node),
            patch.object(
                node, "send_mail", side_effect=Exception("node fail")
            ) as node_send,
            patch("pages.views.send_mail", return_value=1) as fallback,
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
        self.assertContains(resp, "ROLE: Dev")

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
        fixture = Path(settings.BASE_DIR, "pages", "fixtures", "constellation.json")
        call_command("loaddata", str(fixture))
        call_command("loaddata", str(fixture))
        module = Module.objects.get(path="/ocpp/", node_role__name="Constellation")
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
        self.client.force_login(self.user)
        Site.objects.update_or_create(
            id=1, defaults={"name": "test", "domain": "testserver"}
        )

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
        role = NodeRole.objects.create(name="DataRole")
        ud_ct = ContentType.objects.get_for_model(NodeRole)
        UserDatum.objects.create(user=self.user, content_type=ud_ct, object_id=role.pk)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, reverse("admin:pages_application_changelist"))
        self.assertContains(resp, reverse("admin:nodes_noderole_changelist"))

    def test_dashboard_merges_duplicate_future_actions(self):
        ct = ContentType.objects.get_for_model(NodeRole)
        Favorite.objects.create(user=self.user, content_type=ct)
        role = NodeRole.objects.create(name="DataRole2")
        UserDatum.objects.create(user=self.user, content_type=ct, object_id=role.pk)
        AdminHistory.objects.create(
            user=self.user,
            content_type=ct,
            url=reverse("admin:nodes_noderole_changelist"),
        )
        resp = self.client.get(reverse("admin:index"))
        url = reverse("admin:nodes_noderole_changelist")
        self.assertGreaterEqual(resp.content.decode().count(url), 1)
        self.assertContains(resp, NodeRole._meta.verbose_name_plural)

    def test_dashboard_uses_browse_label(self):
        ct = ContentType.objects.get_by_natural_key("pages", "application")
        Favorite.objects.create(user=self.user, content_type=ct)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "Browse Applications")

    def test_dashboard_uses_todo_url_if_set(self):
        Todo.objects.create(description="Check docs", url="/docs/")
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, 'href="/docs/"')

    def test_dashboard_shows_done_button(self):
        todo = Todo.objects.create(description="Do thing")
        resp = self.client.get(reverse("admin:index"))
        done_url = reverse("todo-done", args=[todo.pk])
        self.assertContains(resp, f'action="{done_url}"')
        self.assertContains(resp, "DONE")


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


class EnergyReportLiveUpdateTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_energy_report_includes_interval(self):
        resp = self.client.get(reverse("pages:energy-report"))
        self.assertEqual(resp.context["request"].live_update_interval, 5)
        self.assertContains(resp, "setInterval(() => location.reload()")


class NewsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        NewsArticle.objects.create(
            name="0.1.7 release notes",
            content="Details",
            published=date(2025, 3, 5),
        )
        NewsArticle.objects.create(
            name="0.1.4 release notes",
            content="Details",
            published=date(2024, 7, 10),
        )
        NewsArticle.objects.create(
            name="0.1.1 release notes",
            content="Details",
            published=date(2024, 1, 15),
        )

    def test_latest_article_first(self):
        resp = self.client.get(reverse("pages:news"))
        articles = list(resp.context["object_list"])
        self.assertEqual(articles[0].name, "0.1.7 release notes")

    def test_sidebar_version_order(self):
        resp = self.client.get(reverse("pages:news"))
        titles = [a.name for a in resp.context["all_articles"]]
        self.assertEqual(
            titles,
            [
                "0.1.7 release notes",
                "0.1.4 release notes",
                "0.1.1 release notes",
            ],
        )
