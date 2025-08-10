from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
import socket
from website.models import App


class LoginViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="staff", password="pwd", is_staff=True
        )
        self.user = User.objects.create_user(username="user", password="pwd")
        Site.objects.update_or_create(id=1, defaults={"name": "website"})

    def test_login_link_in_navbar(self):
        resp = self.client.get(reverse("website:index"))
        self.assertContains(resp, 'href="/login/?next=/"')

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
        Node.objects.create(
            hostname=self.node_hostname,
            address=socket.gethostbyname(socket.gethostname()),
        )

    def test_badges_show_site_and_node(self):
        resp = self.client.get(reverse("admin:index"))
        self.assertContains(resp, "SITE: test")
        self.assertContains(resp, f"NODE: {self.node_hostname}")

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
        Site.objects.update_or_create(id=1, defaults={"name": "website"})

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
            id=1, defaults={"name": "example", "domain": "example.com"}
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
    def test_register_current_ip_sets_localhost_name(self):
        resp = self.client.get(
            reverse("admin:website_siteproxy_register_current"), HTTP_HOST="127.0.0.1"
        )
        self.assertRedirects(resp, reverse("admin:website_siteproxy_changelist"))
        site = Site.objects.get(domain="127.0.0.1")
        self.assertEqual(site.name, "localhost")


class AdminBadgesLocalhostTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="badge-admin2", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)
        Site.objects.update_or_create(
            id=1, defaults={"name": "localhost", "domain": "127.0.0.1"}
        )

    @override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"])
    def test_badge_shows_localhost_for_ip_domain(self):
        resp = self.client.get(reverse("admin:index"), HTTP_HOST="127.0.0.1")
        self.assertContains(resp, "SITE: localhost")


class NavAppsTests(TestCase):
    def setUp(self):
        self.client = Client()
        site, _ = Site.objects.update_or_create(
            id=1, defaults={"domain": "127.0.0.1", "name": "localhost"}
        )
        App.objects.create(site=site, name="Readme", path="/", is_default=True)

    def test_nav_pill_renders(self):
        resp = self.client.get(reverse("website:index"))
        self.assertContains(resp, "Readme")
        self.assertContains(resp, "badge rounded-pill")
