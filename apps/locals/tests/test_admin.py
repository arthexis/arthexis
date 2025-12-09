from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from unittest import mock

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

    def test_duplicate_add_falls_back_to_existing_favorite(self):
        url = reverse("admin:favorite_toggle", args=[self.content_type.pk])
        existing = Favorite.objects.create(
            user=self.user,
            content_type=self.content_type,
            custom_label="Original",
            priority=1,
        )

        real_filter = Favorite.objects.filter

        def filter_side_effect(*args, **kwargs):
            if filter_side_effect.called:
                return real_filter(*args, **kwargs)
            filter_side_effect.called = True
            return Favorite.objects.none()

        filter_side_effect.called = False

        with mock.patch(
            "apps.locals.admin.Favorite.objects.filter", side_effect=filter_side_effect
        ), mock.patch(
            "apps.locals.admin.Favorite.objects.create", side_effect=IntegrityError()
        ):
            response = self.client.post(
                url,
                {
                    "next": "/admin/",
                    "custom_label": "Updated",
                    "priority": "3",
                    "user_data": "on",
                },
            )

        self.assertRedirects(response, "/admin/")
        existing.refresh_from_db()
        self.assertEqual(existing.custom_label, "Updated")
        self.assertEqual(existing.priority, 3)
        self.assertTrue(existing.user_data)
