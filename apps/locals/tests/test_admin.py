from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.test import RequestFactory
from django.test import TestCase
from django.urls import reverse
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from unittest import mock

from apps.locals.models import Favorite
from apps.locals.user_data import EntityModelAdmin


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


class RecoverSelectedActionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.content_type_user = ContentType.objects.get_for_model(get_user_model())
        self.content_type_favorite = ContentType.objects.get_for_model(Favorite)
        self.model_admin = EntityModelAdmin(Favorite, admin.site)

    def _build_request(self):
        request = self.factory.post("/admin/")
        request.user = self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)
        return request

    def test_recover_selected_restores_single_deleted_object(self):
        favorite = Favorite.all_objects.create(
            user=self.user,
            content_type=self.content_type_user,
            custom_label="Example",
            priority=1,
            is_deleted=True,
        )
        request = self._build_request()
        queryset = Favorite.all_objects.filter(pk=favorite.pk)

        self.model_admin.recover_selected(request, queryset)

        favorite.refresh_from_db()
        self.assertFalse(favorite.is_deleted)
        messages = [str(message) for message in request._messages]
        self.assertIn("Recovered 1 deleted Favorite.", messages)

    def test_recover_selected_restores_multiple_deleted_objects(self):
        first = Favorite.all_objects.create(
            user=self.user,
            content_type=self.content_type_user,
            custom_label="Example",
            priority=1,
            is_deleted=True,
        )
        second = Favorite.all_objects.create(
            user=self.user,
            content_type=self.content_type_favorite,
            custom_label="Another",
            priority=2,
            is_deleted=True,
        )
        request = self._build_request()
        queryset = Favorite.all_objects.filter(pk__in=[first.pk, second.pk])

        self.model_admin.recover_selected(request, queryset)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_deleted)
        self.assertFalse(second.is_deleted)
        messages = [str(message) for message in request._messages]
        self.assertIn("Recovered 2 deleted Favorites.", messages)
