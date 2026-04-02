import csv
import io
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import IntegrityError
from django.test import RequestFactory
from django.test import TestCase
from django.urls import reverse

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

class ActionChoicesDeduplicationTests(TestCase):
    """Regression tests for admin action choice rendering."""

class ExportColumnOrderTests(TestCase):
    """Regression tests for custom export column ordering."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="exportadmin",
            email="exportadmin@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.factory = RequestFactory()

