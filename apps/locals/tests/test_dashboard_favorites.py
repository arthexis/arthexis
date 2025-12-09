from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from apps.locals.models import Favorite
from apps.locals.templatetags.favorites import favorite_entries


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

    def test_favorites_do_not_duplicate_same_content_type(self):
        content_type = ContentType.objects.get_for_model(Favorite)
        favorite = Favorite.objects.create(
            user=self.user,
            content_type=content_type,
        )

        app_list = [
            {
                "app_label": "locals",
                "models": [
                    {
                        "object_name": "Favorite",
                        "app_label": "locals",
                        "model": Favorite,
                    },
                    {
                        "object_name": "Favorite",
                        "app_label": "locals",
                        "model": Favorite,
                    },
                ],
            }
        ]

        entries = favorite_entries(app_list, {content_type.id: favorite})

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["favorite"], favorite)

    def test_dashboard_favorites_disable_model_badges(self):
        with patch(
            "apps.locals.templatetags.favorites.get_cached_user_favorites",
            return_value="",
        ) as get_cached_user_favorites:
            response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(get_cached_user_favorites.called)
        _, kwargs = get_cached_user_favorites.call_args
        self.assertFalse(kwargs.get("show_model_badges"))
