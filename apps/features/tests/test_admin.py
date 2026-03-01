"""Admin regression tests for suite feature workflows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.features.admin import SourceAppListFilter
from apps.features.models import Feature


@pytest.mark.django_db
def test_feature_admin_change_form_renders_source_as_readonly(admin_client):
    """Regression: source must be displayed as read-only on the change form."""

    feature = Feature.objects.create(
        slug="custom-local-feature",
        display="Custom Local Feature",
        source=Feature.Source.CUSTOM,
    )

    change_url = reverse("admin:features_feature_change", args=[feature.pk])
    response = admin_client.get(change_url)

    assert response.status_code == 200
    assert b"field-source" in response.content
    assert b'class="readonly">Custom<' in response.content


@pytest.mark.django_db
def test_feature_admin_change_form_uses_single_line_autogrow_textareas(admin_client):
    """Regression: feature admin textareas should default to one row and autogrow."""

    feature = Feature.objects.create(
        slug="autogrow-feature",
        display="Autogrow Feature",
        source=Feature.Source.CUSTOM,
    )

    change_url = reverse("admin:features_feature_change", args=[feature.pk])
    response = admin_client.get(change_url)

    assert response.status_code == 200
    assert b'rows="1"' in response.content
    assert b"feature-admin-autogrow" in response.content


@pytest.mark.django_db
def test_feature_admin_reload_base_tool_drops_all_and_loads_fixtures(admin_client):
    """Regression: reload-base tool should clear features and load all mainstream fixtures."""

    Feature.objects.create(slug="custom-a", display="Custom A")
    Feature.objects.create(slug="custom-b", display="Custom B")

    action_url = reverse("admin:features_feature_actions", args=["reload_base"])
    fixture_paths = [
        Path("apps/features/fixtures/features__ocpp_charge_point.json"),
        Path("apps/features/fixtures/features__evergo_api_client.json"),
    ]

    with patch("apps.features.admin.FeatureAdmin._mainstream_fixture_paths", return_value=fixture_paths):
        with patch("apps.features.admin.call_command") as mock_call_command:
            response = admin_client.post(action_url, follow=True)

    assert response.status_code == 200
    assert Feature.objects.count() == 0
    mock_call_command.assert_called_once_with(
        "load_user_data", *(str(path) for path in fixture_paths), verbosity=0
    )


@pytest.mark.django_db
def test_feature_admin_reload_base_requires_delete_permission(admin_client, django_user_model):
    """Regression: reload-base must enforce model delete permission."""

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
def test_feature_admin_from_app_filter_shows_only_referenced_apps(rf):
    """Regression: from-app filter should only include apps referenced by suite features."""

    from apps.app.models import Application

    app_with_feature = Application.objects.create(name="app-with-feature")
    Application.objects.create(name="unused-app")
    Feature.objects.create(
        slug="feature-with-main-app",
        display="Feature With Main App",
        source=Feature.Source.CUSTOM,
        main_app=app_with_feature,
    )

    request = rf.get("/admin/features/feature/")
    list_filter = SourceAppListFilter(request, {}, Feature, admin.site)

    lookup_values = {label for _, label in list_filter.lookups(request, admin.site)}
    assert "app-with-feature" in lookup_values
    assert "unused-app" not in lookup_values


@pytest.mark.django_db
def test_feature_admin_from_app_filter_limits_results(rf):
    """Regression: from-app filter should limit changelist rows to selected app."""

    from apps.app.models import Application

    target_app = Application.objects.create(name="target-app")
    other_app = Application.objects.create(name="other-app")
    matching = Feature.objects.create(
        slug="matching-feature",
        display="Matching Feature",
        source=Feature.Source.CUSTOM,
        main_app=target_app,
    )
    Feature.objects.create(
        slug="non-matching-feature",
        display="Non Matching Feature",
        source=Feature.Source.CUSTOM,
        main_app=other_app,
    )

    request = rf.get("/admin/features/feature/", {"main_app": str(target_app.pk)})
    list_filter = SourceAppListFilter(request, {"main_app": str(target_app.pk)}, Feature, admin.site)

    filtered = list_filter.queryset(request, Feature.objects.all())
    assert set(filtered.values_list("pk", flat=True)) == {matching.pk}
