from django.test import Client, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site


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
        self.assertContains(resp, "href=\"/login/?next=/\"")

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

