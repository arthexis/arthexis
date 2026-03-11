"""Regression tests for project bundle admin workflows."""

from __future__ import annotations

import io
import json
import zipfile

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.features.models import Feature, FeatureTest
from apps.locals.models import Favorite
from apps.locals.user_data import EntityModelAdmin
from apps.projects.models import Project, ProjectItem


class ProjectAdminActionTests(TestCase):
    """Verify entity changelist actions can add objects to a project."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="projectadmin",
            email="projectadmin@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.project = Project.objects.create(name="Alpha")
        self.favorite = Favorite.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(get_user_model()),
            custom_label="Bundle Favorite",
            priority=0,
        )
        self.model_admin = EntityModelAdmin(Favorite, admin.site)
        self.factory = RequestFactory()

    def _build_request(self):
        request = self.factory.post("/admin/", {"apply": "1", "project": self.project.pk})
        request.user = self.user
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        messages_storage = FallbackStorage(request)
        setattr(request, "_messages", messages_storage)
        return request

    def test_add_selected_to_project_action_creates_project_item(self):
        """Regression: Add selected to Project should create a project item."""

        request = self._build_request()
        queryset = Favorite.objects.filter(pk=self.favorite.pk)

        self.model_admin.add_selected_to_project(request, queryset)

        self.assertTrue(
            ProjectItem.objects.filter(
                project=self.project,
                content_type=ContentType.objects.get_for_model(
                    self.favorite,
                    for_concrete_model=False,
                ),
                object_id=str(self.favorite.pk),
            ).exists()
        )


class ProjectBundleTests(TestCase):
    """Verify project bundle import and export endpoints."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="bundleadmin",
            email="bundleadmin@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Bundle A")
        self.feature = Feature.objects.create(slug="bundle-export", display="Export")

    def test_export_and_import_bundle_zip(self):
        """Regression: project bundles should export and import as a ZIP archive."""

        self.project.items.create(
            content_type=ContentType.objects.get_for_model(
                Feature,
                for_concrete_model=False,
            ),
            object_id=str(self.feature.pk),
        )

        export_response = self.client.get(
            reverse("admin:projects_project_bundle_export", args=[self.project.pk])
        )

        self.assertEqual(export_response.status_code, 200)
        self.assertEqual(export_response["Content-Type"], "application/zip")

        imported_project = Project.objects.create(name="Bundle B")
        fileobj = io.BytesIO(export_response.content)
        with zipfile.ZipFile(fileobj, "r") as archive:
            self.assertIn("project.json", archive.namelist())
            self.assertIn("objects.json", archive.namelist())
            self.assertIn("items.json", archive.namelist())

        fileobj.seek(0)
        import_response = self.client.post(
            reverse("admin:projects_project_bundle_import", args=[imported_project.pk]),
            {
                "bundle_file": SimpleUploadedFile(
                    "bundle.zip",
                    fileobj.read(),
                    content_type="application/zip",
                )
            },
            follow=True,
        )

        self.assertEqual(import_response.status_code, 200)
        self.assertEqual(imported_project.items.count(), 1)


    def test_bundle_import_rejects_models_without_add_permission(self):
        """Regression: import must reject models the user cannot add."""

        restricted_user = get_user_model().objects.create_user(
            username="bundleeditor",
            email="bundleeditor@example.com",
            password="password",
            is_staff=True,
            is_superuser=False,
        )
        permission_models = {
            "change_project": Project,
            "view_project": Project,
            "view_projectitem": ProjectItem,
            "add_projectitem": ProjectItem,
        }
        for codename, model in permission_models.items():
            restricted_user.user_permissions.add(
                Permission.objects.get(
                    codename=codename,
                    content_type=ContentType.objects.get_for_model(model),
                )
            )
        self.client.force_login(restricted_user)

        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "objects.json",
                json.dumps(
                    [
                        {
                            "model": "auth.user",
                            "pk": 999,
                            "fields": {
                                "password": "pbkdf2_sha256$720000$fake$hash",
                                "last_login": None,
                                "is_superuser": True,
                                "username": "haxor",
                                "first_name": "",
                                "last_name": "",
                                "email": "haxor@example.com",
                                "is_staff": True,
                                "is_active": True,
                                "date_joined": "2024-01-01T00:00:00Z",
                                "groups": [],
                                "user_permissions": [],
                            },
                        }
                    ]
                ),
            )
            archive.writestr(
                "items.json",
                json.dumps([{"model": "auth.user", "object_id": "999", "note": ""}]),
            )

        payload.seek(0)
        response = self.client.post(
            reverse("admin:projects_project_bundle_import", args=[self.project.pk]),
            {
                "bundle_file": SimpleUploadedFile(
                    "bundle.zip",
                    payload.read(),
                    content_type="application/zip",
                )
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unable to import project bundle")
        self.assertFalse(get_user_model().objects.filter(username="haxor").exists())

    def test_bundle_import_invalid_zip_shows_error(self):
        """Regression: invalid archives should not raise 500 errors."""

        response = self.client.post(
            reverse("admin:projects_project_bundle_import", args=[self.project.pk]),
            {
                "bundle_file": SimpleUploadedFile(
                    "bundle.zip",
                    b"not-a-zip",
                    content_type="application/zip",
                )
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unable to import project bundle")

    def test_bundle_view_requires_project_permissions(self):
        """Regression: users without project perms must not access bundle routes."""

        limited_user = get_user_model().objects.create_user(
            username="limited",
            email="limited@example.com",
            password="password",
            is_staff=True,
            is_superuser=False,
        )
        self.client.force_login(limited_user)

        bundle_response = self.client.get(
            reverse("admin:projects_project_bundle", args=[self.project.pk])
        )
        import_response = self.client.post(
            reverse("admin:projects_project_bundle_import", args=[self.project.pk]),
            {
                "bundle_file": SimpleUploadedFile(
                    "bundle.zip",
                    b"not-a-zip",
                    content_type="application/zip",
                )
            },
        )

        self.assertEqual(bundle_response.status_code, 403)
        self.assertEqual(import_response.status_code, 403)

    def test_bundle_view_shows_project_metadata_and_export_section(self):
        """Regression: bundle view should show project details and export controls."""

        self.project.description = "Bundle description"
        self.project.save(update_fields=["description"])

        response = self.client.get(
            reverse("admin:projects_project_bundle", args=[self.project.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Back to project: Bundle A")
        self.assertContains(response, "Project details")
        self.assertContains(response, "Bundle description")
        self.assertContains(response, "Import bundle")
        self.assertContains(response, "Update bundle")
        self.assertContains(response, "Export bundle")

    def test_bundle_selection_removes_unchecked_items(self):
        """Regression: unselected bundle rows should be removed from the project."""

        second_feature = FeatureTest.objects.create(
            feature=self.feature,
            node_id="tests::export-second",
            name="Export Test 2",
        )
        first_item = self.project.items.create(
            content_type=ContentType.objects.get_for_model(
                Feature,
                for_concrete_model=False,
            ),
            object_id=str(self.feature.pk),
        )
        second_item = self.project.items.create(
            content_type=ContentType.objects.get_for_model(
                FeatureTest,
                for_concrete_model=False,
            ),
            object_id=str(second_feature.pk),
        )

        response = self.client.post(
            reverse("admin:projects_project_bundle", args=[self.project.pk]),
            {
                "_bundle_selection": "1",
                "selected_items": [str(first_item.pk)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(ProjectItem.objects.filter(pk=first_item.pk).exists())
        self.assertFalse(ProjectItem.objects.filter(pk=second_item.pk).exists())
