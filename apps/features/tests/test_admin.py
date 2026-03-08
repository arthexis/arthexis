"""Admin regression tests for suite feature workflows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import override_settings
from django.urls import reverse

from apps.features.admin import FeatureAdmin, FeatureAdminForm
from apps.features.admin import SourceAppListFilter
from apps.features.models import Feature


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


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
@override_settings(STORAGES=TEST_STORAGES)
def test_feature_admin_toggle_selected_feature_requires_change_permission(admin_client, django_user_model):
    """Regression: bulk toggle action must not execute for view-only admins."""

    feature = Feature.objects.create(slug="view-only-target", display="View Only Target", is_enabled=True)

    user = django_user_model.objects.create_user(
        username="view-only-admin",
        email="view-only@example.com",
        password="pass",
        is_staff=True,
    )
    view_perm = Permission.objects.get(codename="view_feature", content_type__app_label="features")
    user.user_permissions.set([view_perm])
    admin_client.force_login(user)

    changelist_url = reverse("admin:features_feature_changelist")
    response = admin_client.post(
        changelist_url,
        {
            "action": "toggle_selected_feature",
            "_selected_action": [str(feature.pk)],
        },
        follow=True,
    )

    assert response.status_code == 200
    feature.refresh_from_db()
    assert feature.is_enabled is True
    action_messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert action_messages
    assert not any("Toggled " in message for message in action_messages)


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


@pytest.mark.django_db
@override_settings(STORAGES=TEST_STORAGES)
def test_feature_admin_saving_celery_workers_feature_syncs_runtime(admin_client):
    """Regression: saving celery-workers parameters triggers runtime sync."""

    feature = Feature.objects.get(slug="celery-workers")
    feature.metadata = {"parameters": {"worker_count": "1"}}
    feature.save(update_fields=["metadata", "updated_at"])

    with patch("apps.features.admin.sync_celery_workers_from_feature") as sync_runtime:
        response = admin_client.post(
            reverse("admin:features_feature_change", args=[feature.pk]),
            {
                "display": "Celery Workers",
                "slug": "celery-workers",
                "summary": "",
                "is_enabled": "on",
                "main_app": "",
                "node_feature": "",
                "admin_requirements": "",
                "public_requirements": "",
                "service_requirements": "",
                "admin_views": "[]",
                "public_views": "[]",
                "service_views": "[]",
                "metadata": "{}",
                "code_locations": "[]",
                "protocol_coverage": "{}",
                "param__worker_count": "5",
                "featuretest_set-TOTAL_FORMS": "0",
                "featuretest_set-INITIAL_FORMS": "0",
                "featuretest_set-MIN_NUM_FORMS": "0",
                "featuretest_set-MAX_NUM_FORMS": "1000",
                "featurenote_set-TOTAL_FORMS": "0",
                "featurenote_set-INITIAL_FORMS": "0",
                "featurenote_set-MIN_NUM_FORMS": "0",
                "featurenote_set-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
        )

    assert response.status_code == 302
    sync_runtime.assert_called_once_with()



