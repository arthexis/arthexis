"""Focused admin authorization regression tests for actions app."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.integration
def test_remote_action_openapi_forbidden_for_unprivileged_staff(client):
    """Ensure staff without RemoteAction permissions cannot access OpenAPI output."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="openapi_staff_no_remoteaction_perm",
        password="test-password",
        is_staff=True,
    )
    client.force_login(user)

    response = client.get(reverse("admin:actions_remoteaction_my_openapi_spec"))
    assert response.status_code == 403

    download_response = client.get(
        reverse("admin:actions_remoteaction_my_openapi_spec"),
        data={"download": "1"},
    )
    assert download_response.status_code == 403
