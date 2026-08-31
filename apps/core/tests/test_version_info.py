from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse

from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
from apps.groups.models import SecurityGroup

pytestmark = [pytest.mark.django_db]


def test_version_info_allows_staff(admin_client):
    response = admin_client.get(reverse("version-info"))

    assert response.status_code == 200
    assert {"version", "revision"} <= set(response.json())


def test_version_info_allows_site_operator(client):
    user = get_user_model().objects.create_user(username="site-operator")
    group = SecurityGroup.objects.create(name=SITE_OPERATOR_GROUP_NAME)
    user.groups.add(group)
    client.force_login(user)

    response = client.get(reverse("version-info"))

    assert response.status_code == 200
    assert {"version", "revision"} <= set(response.json())


def test_version_info_rejects_regular_authenticated_user(client):
    user = get_user_model().objects.create_user(username="regular-viewer")
    client.force_login(user)

    response = client.get(reverse("version-info"))

    assert response.status_code == 403


def test_version_info_rejects_anonymous_user(client):
    response = client.get(reverse("version-info"))

    assert response.status_code == 403


def test_version_check_template_hides_local_metadata_without_authorized_signal():
    html = render_to_string(
        "core/version_check.html",
        {
            "version_check_allowed": False,
        },
    )

    assert 'const LOCAL_VERSION = "";' in html
    assert 'const LOCAL_REVISION = "";' in html


def test_version_check_template_ignores_cached_payload_when_local_metadata_exists():
    html = render_to_string(
        "core/version_check.html",
        {
            "version_check_allowed": True,
        },
    )

    assert "if (!result.fromCache || !hasLocalInfo) {" in html
    assert "applyRemoteState(result.data);" in html
