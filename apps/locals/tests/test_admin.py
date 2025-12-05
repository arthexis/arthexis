from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from apps.locals.models import Favorite


class FavoriteToggleViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="testuser",
            email="test@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.content_type = ContentType.objects.get_for_model(Favorite)

    def test_get_renders_confirmation_for_new_favorite(self):
        url = reverse("admin:favorite_toggle", args=[self.content_type.pk])

        response = self.client.get(url, {"next": "/admin/"})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/favorite_confirm.html")
        self.assertContains(response, "Add Favorite")
