"""Admin regression tests for suite feature workflows."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.features.models import Feature


@pytest.mark.django_db
def test_feature_admin_toggle_selected_feature_action_flips_enabled_state(admin_client):
    """Regression: changelist action must invert enabled state for selected features."""

    feature_enabled = Feature.objects.create(
        slug="toggle-enabled",
        display="Toggle Enabled",
        source=Feature.Source.CUSTOM,
        is_enabled=True,
    )
    feature_disabled = Feature.objects.create(
        slug="toggle-disabled",
        display="Toggle Disabled",
        source=Feature.Source.CUSTOM,
        is_enabled=False,
    )

    changelist_url = reverse("admin:features_feature_changelist")
    response = admin_client.post(
        changelist_url,
        {
            "action": "toggle_selected_feature",
            "_selected_action": [str(feature_enabled.pk), str(feature_disabled.pk)],
        },
    )

    assert response.status_code == 302

    feature_enabled.refresh_from_db()
    feature_disabled.refresh_from_db()

    assert feature_enabled.is_enabled is False
    assert feature_disabled.is_enabled is True

@pytest.mark.django_db
def test_feature_admin_reload_base_requires_delete_permission(admin_client, django_user_model):
    """Regression: reload-all must enforce model delete permission."""

    user = django_user_model.objects.create_user(
        username="limited-admin",
        email="limited@example.com",
        password="pass",
        is_staff=True,
    )
    perms = Permission.objects.filter(
        codename__in=["view_feature", "change_feature"], content_type__app_label="features"
    )
    user.user_permissions.set(perms)
    admin_client.force_login(user)

    action_url = reverse("admin:features_feature_actions", args=["reload_base"])
    response = admin_client.post(action_url)

    assert response.status_code == 403

