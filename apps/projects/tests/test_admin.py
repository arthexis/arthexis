"""Focused project admin security regression tests."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.projects.models import Project, ProjectItem


@pytest.mark.django_db
@pytest.mark.integration
def test_bundle_import_rejects_models_without_add_permission(client):
    """Ensure bundle import rejects model payloads a non-superuser cannot add."""

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

    client.force_login(restricted_user)
    project = Project.objects.create(name="Bundle A")

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
    response = client.post(
        reverse("admin:projects_project_bundle_import", args=[project.pk]),
        {
            "bundle_file": SimpleUploadedFile(
                "bundle.zip",
                payload.read(),
                content_type="application/zip",
            )
        },
        follow=True,
    )

    assert response.status_code == 200
    assert "Unable to import project bundle" in response.content.decode()
    assert not get_user_model().objects.filter(username="haxor").exists()
