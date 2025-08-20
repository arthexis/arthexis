from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.contrib import admin
from django.core.exceptions import DisallowedHost
import socket
from website.models import Application, SiteApplication
from website.admin import ApplicationAdmin


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
        resp = self.client.get(reverse("website:index"))
        self.assertContains(resp, 'href="/login/"')

    def test_staff_login_redirects_admin(self):
        resp = self.client.post(
            reverse("website:login"),
            {"username": "staff", "password": "pwd"},
        )
        self.assertRedirects(resp, reverse("admin:index"))

    def test_already_logged_in_staff_redirects(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("website:login"))
        self.assertRedirects(resp, reverse("admin:index"))

    def test_regular_user_redirects_next(self):
        resp = self.client.post(
            reverse("website:login") + "?next=/nodes/list/",
            {"username": "user", "password": "pwd"},
        )
        self.assertRedirects(resp, "/nodes/list/")

    def test_staff_redirects_next_when_specified(self):
        resp = self.client.post(
            reverse("website:login") + "?next=/nodes/list/",
            {"username": "staff", "password": "pwd"},
        )
        self.assertRedirects(resp, "/nodes/list/")


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

    def test_badges_show_node_roles(self):
        from nodes.models import NodeRole

        role1 = NodeRole.objects.create(name="Dev")
        role2 = NodeRole.objects.create(name="Proxy")
        self.node.roles.add(role1, role2)
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "ROLE: Dev")
        self.assertContains(resp, "ROLE: Proxy")

    def test_badges_warn_when_node_missing(self):
        from nodes.models import Node

        Node.objects.all().delete()
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "NODE: Unknown")
        self.assertContains(resp, "badge-unknown")
        self.assertContains(resp, "#6c757d")


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


class ReadmeSidebarTests(TestCase):
    def setUp(self):
        self.client = Client()
        Site.objects.update_or_create(id=1, defaults={"name": "Terminal"})

    def test_table_of_contents_sidebar_present(self):
        resp = self.client.get(reverse("website:index"))
        self.assertIn("toc", resp.context)
        self.assertContains(resp, 'class="toc"')
        html = resp.content.decode()
        self.assertLess(html.index('nav class="toc"'), html.index('class="col-lg-9"'))

    def test_included_apps_table_renders(self):
        resp = self.client.get(reverse("website:index"))
        self.assertContains(resp, "<table")
        self.assertContains(resp, "<td>accounts</td>")


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
        resp = self.client.get(reverse("admin:website_siteproxy_changelist"))
        self.assertContains(resp, "Register Current")

        resp = self.client.get(reverse("admin:website_siteproxy_register_current"))
        self.assertRedirects(resp, reverse("admin:website_siteproxy_changelist"))
        self.assertTrue(Site.objects.filter(domain="testserver").exists())
        site = Site.objects.get(domain="testserver")
        self.assertEqual(site.name, "testserver")

    @override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"])
    def test_register_current_ip_sets_website_name(self):
        resp = self.client.get(
            reverse("admin:website_siteproxy_register_current"), HTTP_HOST="127.0.0.1"
        )
        self.assertRedirects(resp, reverse("admin:website_siteproxy_changelist"))
        site = Site.objects.get(domain="127.0.0.1")
        self.assertEqual(site.name, "Terminal")


class AdminBadgesWebsiteTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="badge-admin2", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "Terminal", "domain": "127.0.0.1"}
        )

    @override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"])
    def test_badge_shows_website_for_ip_domain(self):
        resp = self.client.get(reverse("admin:index"), HTTP_HOST="127.0.0.1")
        self.assertContains(resp, "SITE: Terminal")


class NavAppsTests(TestCase):
    def setUp(self):
        self.client = Client()
        site, _ = Site.objects.update_or_create(
            id=1, defaults={"domain": "127.0.0.1", "name": "Terminal"}
        )
        app = Application.objects.create(name="Readme")
        SiteApplication.objects.create(
            site=site, application=app, path="/", is_default=True
        )

    def test_nav_pill_renders(self):
        resp = self.client.get(reverse("website:index"))
        self.assertContains(resp, "README")
        self.assertContains(resp, "badge rounded-pill")

    def test_nav_pill_renders_with_port(self):
        resp = self.client.get(reverse("website:index"), HTTP_HOST="127.0.0.1:8000")
        self.assertContains(resp, "README")

    def test_app_without_root_url_excluded(self):
        site = Site.objects.get(id=1)
        app = Application.objects.create(name="accounts")
        SiteApplication.objects.create(site=site, application=app, path="/accounts/")
        resp = self.client.get(reverse("website:index"))
        self.assertNotContains(resp, 'href="/accounts/"')


class ApplicationModelTests(TestCase):
    def test_path_defaults_to_slugified_name(self):
        site, _ = Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "website"}
        )
        app = Application.objects.create(name="accounts")
        site_app = SiteApplication.objects.create(site=site, application=app)
        self.assertEqual(site_app.path, "/accounts/")

    def test_installed_flag_false_when_missing(self):
        app = Application.objects.create(name="missing")
        self.assertFalse(app.installed)


class ApplicationAdminFormTests(TestCase):
    def test_name_field_uses_local_apps(self):
        admin_instance = ApplicationAdmin(Application, admin.site)
        form = admin_instance.get_form(request=None)()
        choices = [choice[0] for choice in form.fields["name"].choices]
        self.assertIn("accounts", choices)


class AllowedHostSubnetTests(TestCase):
    def setUp(self):
        self.client = Client()
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "website"}
        )

    @override_settings(ALLOWED_HOSTS=["10.42.0.0/16", "192.168.0.0/16"])
    def test_private_network_hosts_allowed(self):
        resp = self.client.get(
            reverse("website:index"), HTTP_HOST="10.42.1.5"
        )
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(
            reverse("website:index"), HTTP_HOST="192.168.2.3"
        )
        self.assertEqual(resp.status_code, 200)

    @override_settings(ALLOWED_HOSTS=["10.42.0.0/16"])
    def test_host_outside_subnets_disallowed(self):
        resp = self.client.get(
            reverse("website:index"), HTTP_HOST="11.0.0.1"
        )
        self.assertEqual(resp.status_code, 400)


class RFIDPageTests(TestCase):
    def setUp(self):
        self.client = Client()
        Site.objects.update_or_create(
            id=1, defaults={"domain": "testserver", "name": "website"}
        )

    def test_page_renders(self):
        resp = self.client.get(reverse("rfid-reader"))
        self.assertContains(resp, "Scanner ready")
