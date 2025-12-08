from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from apps.locals.models import Favorite


class DashboardFavoritesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="favuser",
            email="fav@example.com",
            password="password",
        )
        self.client.force_login(self.user)
        self.dashboard_url = reverse("admin:index")

    def test_favorites_render_after_cache_was_empty(self):
        # First request with no favorites populates an empty cache entry.
        self.client.get(self.dashboard_url)

        Favorite.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(get_user_model()),
        )

        response = self.client.get(self.dashboard_url)

        self.assertContains(response, "favorites-users-user")
