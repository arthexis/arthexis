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
@override_settings(STORAGES=TEST_STORAGES)
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
@override_settings(STORAGES=TEST_STORAGES)
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
def test_feature_admin_toggle_selected_feature_action_reports_counts(admin_client):
    """Regression: changelist action should report enabled/disabled totals after toggling."""

    feature_a = Feature.objects.create(slug="toggle-a", display="Toggle A", is_enabled=True)
    feature_b = Feature.objects.create(slug="toggle-b", display="Toggle B", is_enabled=False)

    changelist_url = reverse("admin:features_feature_changelist")
    response = admin_client.post(
        changelist_url,
        {
            "action": "toggle_selected_feature",
            "_selected_action": [str(feature_a.pk), str(feature_b.pk)],
        },
    )

    assert response.status_code == 302
    action_messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("Toggled 2 suite features (1 enabled, 1 disabled)." in message for message in action_messages)


@pytest.mark.django_db
@override_settings(STORAGES=TEST_STORAGES)
def test_feature_admin_reload_all_preview_renders_expected_change_summary(admin_client):
    """Regression: reload-all tool should first render a change summary confirmation view."""

    Feature.objects.create(slug="custom-a", display="Custom A")
    Feature.objects.create(slug="custom-b", display="Custom B")

    action_url = reverse("admin:features_feature_actions", args=["reload_base"])
    fixture_paths = [
        Path("apps/features/fixtures/features__ocpp_charge_point.json"),
        Path("apps/features/fixtures/features__evergo_api_client.json"),
    ]

    with patch("apps.features.admin.FeatureAdmin._mainstream_fixture_paths", return_value=fixture_paths):
        response = admin_client.get(action_url)

    assert response.status_code == 200
    assert b"Reload all suite features" in response.content
    assert b"This will delete" in response.content
    assert b"existing suite feature" in response.content
    assert b"2 fixtures will be loaded" in response.content


@pytest.mark.django_db
@override_settings(STORAGES=TEST_STORAGES)
def test_feature_admin_reload_all_tool_drops_all_and_loads_fixtures(admin_client):
    """Regression: confirmed reload-all must clear features and load all mainstream fixtures."""

    Feature.objects.create(slug="custom-a", display="Custom A")
    Feature.objects.create(slug="custom-b", display="Custom B")

    action_url = reverse("admin:features_feature_actions", args=["reload_base"])
    fixture_paths = [
        Path("apps/features/fixtures/features__ocpp_charge_point.json"),
        Path("apps/features/fixtures/features__evergo_api_client.json"),
    ]

    with patch("apps.features.admin.FeatureAdmin._mainstream_fixture_paths", return_value=fixture_paths):
        with patch("apps.features.admin.call_command") as mock_call_command:
            response = admin_client.post(action_url, {"confirm": "yes"}, follow=True)

    assert response.status_code == 200
    assert Feature.objects.count() == 0
    mock_call_command.assert_called_once_with(
        "load_user_data", *(str(path) for path in fixture_paths), verbosity=0
    )


def test_feature_admin_reload_all_action_label_is_updated():
    """Regression: suite feature object action metadata should use the Reload All label."""

    assert str(FeatureAdmin.reload_base.label) == "Reload All"
    assert str(FeatureAdmin.reload_base.short_description) == "Reload All"


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
    feature_admin = admin.site._registry[Feature]
    list_filter = SourceAppListFilter(request, {}, Feature, feature_admin)

    lookup_values = {label for _, label in list_filter.lookups(request, feature_admin)}
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
    feature_admin = admin.site._registry[Feature]
    list_filter = SourceAppListFilter(request, {"main_app": str(target_app.pk)}, Feature, feature_admin)

    filtered = list_filter.queryset(request, Feature.objects.all())
    assert set(filtered.values_list("pk", flat=True)) == {matching.pk}


@pytest.mark.django_db
def test_feature_admin_from_app_filter_uses_admin_queryset_scope(rf):
    """Regression: from-app filter should respect admin queryset scope (e.g., deleted view)."""

    from apps.app.models import Application

    deleted_app = Application.objects.create(name="deleted-app")
    active_app = Application.objects.create(name="active-app")

    deleted_feature = Feature.objects.create(
        slug="deleted-seed-feature",
        display="Deleted Seed Feature",
        source=Feature.Source.MAINSTREAM,
        main_app=deleted_app,
        is_seed_data=True,
        is_enabled=False,
    )
    Feature.objects.create(
        slug="active-feature",
        display="Active Feature",
        source=Feature.Source.CUSTOM,
        main_app=active_app,
    )
    deleted_feature.delete()

    request = rf.get("/admin/features/feature/deleted/")
    request._soft_deleted_only = True
    feature_admin = admin.site._registry[Feature]

    list_filter = SourceAppListFilter(request, {}, Feature, feature_admin)

    lookup_values = {label for _, label in list_filter.lookups(request, feature_admin)}
    assert "deleted-app" in lookup_values
    assert "active-app" not in lookup_values


@pytest.mark.django_db
@override_settings(STORAGES=TEST_STORAGES)
def test_feature_admin_operator_site_language_parameter_is_editable(admin_client):
    """Regression: operator interface language parameter should render and persist in admin."""

    feature, _created = Feature.objects.update_or_create(
        slug="operator-site-interface",
        defaults={
            "display": "Operator Site Interface",
            "source": Feature.Source.CUSTOM,
            "metadata": {"parameters": {"default_language": "en"}},
        },
    )

    change_url = reverse("admin:features_feature_change", args=[feature.pk])
    response = admin_client.get(change_url)

    assert response.status_code == 200
    assert b"Feature parameters" in response.content
    assert b'id="id_param__default_language"' in response.content

    form_data = {
        "display": feature.display,
        "slug": feature.slug,
        "summary": "",
        "is_enabled": True,
        "main_app": "",
        "node_feature": "",
        "user": "",
        "group": "",
        "admin_requirements": "",
        "public_requirements": "",
        "service_requirements": "",
        "admin_views": "[]",
        "public_views": "[]",
        "service_views": "[]",
        "code_locations": "[]",
        "protocol_coverage": "{}",
        "metadata": '{"parameters": {"default_language": "en"}}',
        "param__default_language": "es",
    }
    form = FeatureAdminForm(data=form_data, instance=feature)
    assert form.is_valid(), form.errors
    assert form.cleaned_parameter_values() == {"default_language": "es"}

    save_data = dict(form_data)
    save_data["_save"] = "Save"
    post_response = admin_client.post(change_url, data=save_data)
    assert post_response.status_code == 302

    feature.refresh_from_db()
    assert feature.metadata["parameters"]["default_language"] == "es"
