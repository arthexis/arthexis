from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AdminLoginCredentialTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.login_url = reverse("admin:login")
        self.index_url = reverse("admin:index")

    def test_admin_can_login_with_default_credentials(self):
        self.User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="admin",
        )

        login_page = self.client.get(self.login_url)
        self.assertEqual(login_page.status_code, 200)

        response = self.client.post(
            self.login_url,
            {
                "username": "admin",
                "password": "admin",
                "next": self.index_url,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request.get("PATH_INFO"), self.index_url)
        self.assertContains(response, "Site administration")
        self.assertTrue(response.wsgi_request.user.is_authenticated)
