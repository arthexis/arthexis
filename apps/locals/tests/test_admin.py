from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import IntegrityError
from django.test import RequestFactory
from django.test import TestCase
from django.urls import reverse
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

    def test_get_creates_default_favorite_and_redirects_to_changelist(self):
        """Regression: new favorites should be created with defaults from blue stars."""

        url = reverse("admin:favorite_toggle", args=[self.content_type.pk])

        response = self.client.get(url, {"next": "/admin/"})

        self.assertRedirects(response, "/admin/")
        favorite = Favorite.objects.get(user=self.user, content_type=self.content_type)
        self.assertEqual(favorite.custom_label, "")
        self.assertEqual(favorite.priority, 0)
        self.assertTrue(favorite.user_data)
        self.assertTrue(favorite.is_user_data)

    def test_duplicate_add_falls_back_to_existing_favorite(self):
        """Regression: race-condition duplicate add should update existing favorite."""

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
        self.assertTrue(existing.is_user_data)


class FavoriteListViewTests(TestCase):
    """Regression tests for favorite list bulk edits."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="favlistuser",
            email="favlist@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.content_type = ContentType.objects.get_for_model(Favorite)

    def test_post_updates_custom_label_and_user_data(self):
        """Regression: favorites list saves label and user-data fields."""

        favorite = Favorite.objects.create(
            user=self.user,
            content_type=self.content_type,
            custom_label="Before",
            priority=1,
        )
        Favorite.all_objects.filter(pk=favorite.pk).update(
            user_data=False,
            is_user_data=False,
        )

        response = self.client.post(
            reverse("admin:favorite_list"),
            {
                f"custom_label_{favorite.pk}": "After",
                "user_data": [str(favorite.pk)],
            },
        )

        self.assertRedirects(response, reverse("admin:favorite_list"))
        favorite.refresh_from_db()
        self.assertEqual(favorite.custom_label, "After")
        self.assertEqual(favorite.priority, 1)
        self.assertTrue(favorite.user_data)
        self.assertTrue(favorite.is_user_data)


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


class ActionChoicesDeduplicationTests(TestCase):
    """Regression tests for admin action choice rendering."""

    def test_get_action_choices_deduplicates_duplicate_values(self):
        """Regression: duplicated action values should appear only once in choices."""

        model_admin = EntityModelAdmin(Favorite, admin.site)
        request = RequestFactory().get("/admin/locals/favorite/")
        request.user = get_user_model()(is_staff=True, is_superuser=True, is_active=True)

        with mock.patch.object(
            admin.ModelAdmin,
            "get_action_choices",
            return_value=[
                ("", "---------"),
                ("", "---------"),
                ("recover_selected", "Recover selected entries"),
                ("recover_selected", "Recover selected entries"),
            ],
        ):
            choices = model_admin.get_action_choices(request)

        self.assertEqual(
            choices,
            [
                ("", "---------"),
                ("recover_selected", "Recover selected entries"),
            ],
        )
