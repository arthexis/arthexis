"""Tests for extension admin customizations."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.extensions.models import JsExtension

pytestmark = pytest.mark.django_db

def test_admin_download_archive_view_requires_model_permission():
    """Deny archive downloads for staff users without extension permissions."""
    extension = JsExtension.objects.create(
        slug="helper-permissions",
        name="Helper",
        version="1.0.0",
        matches="https://example.com/*",
    )

    user = get_user_model().objects.create_user(
        username="staff-no-extension-access",
        password="secret",
        is_staff=True,
    )
    client = Client()
    client.force_login(user)

    response = client.get(
        reverse("admin:extensions_jsextension_download", args=[extension.pk])
    )

    assert response.status_code == 403

