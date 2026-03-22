"""Admin regression tests for suite feature workflows."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import RequestFactory
from django.test import override_settings
from django.urls import reverse

from apps.app.models import Application
from apps.features.admin import FeatureAdmin, SourceAppListFilter
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
def test_feature_admin_changelist_hides_owner_and_node_feature_filters():
    """Regression: suite feature admin changelist must not show owner or node-feature filters."""

    admin_instance = FeatureAdmin(Feature, admin.site)

    assert "owner_label" not in admin_instance.get_list_display(request=None)
    assert "node_feature" not in admin_instance.get_list_filter(request=None)


@pytest.mark.django_db
def test_feature_admin_form_excludes_ownership_fields_for_change_view(django_user_model):
    """Regression: suite feature admin form should not expose ownership controls."""

    feature = Feature.objects.create(slug="admin-no-owner", display="Admin No Owner")
    request = RequestFactory().get("/")
    request.user = django_user_model.objects.create_superuser(
        username="admin-form-user",
        email="admin-form@example.com",
        password="pass",
    )
    admin_instance = FeatureAdmin(Feature, admin.site)

    form_class = admin_instance.get_form(request, obj=feature)

    assert "user" not in form_class.base_fields
    assert "group" not in form_class.base_fields



@pytest.mark.django_db
def test_feature_admin_form_supports_dynamic_parameter_fieldsets(django_user_model):
    """Regression: parameterized suite feature change forms must render without FieldError."""

    feature = Feature.objects.create(slug="ocpp-simulator", display="OCPP Simulator")
    request = RequestFactory().get("/")
    request.user = django_user_model.objects.create_superuser(
        username="admin-dynamic-form-user",
        email="admin-dynamic-form@example.com",
        password="pass",
    )
    admin_instance = FeatureAdmin(Feature, admin.site)

    form_class = admin_instance.get_form(request, obj=feature)

    assert "param__arthexis_backend" in form_class.base_fields
    assert "param__mobilityhouse_backend" in form_class.base_fields


@pytest.mark.django_db
def test_feature_admin_source_app_filter_lookups_include_feature_apps(rf):
    """Regression: source-app list filter lookups should resolve app labels safely."""

    app = Application.objects.create(name="admin-filter-app")
    Feature.objects.create(slug="filter-target", display="Filter Target", main_app=app)

    request = rf.get("/")
    admin_instance = FeatureAdmin(Feature, admin.site)
    source_app_filter = next(
        list_filter for list_filter in admin_instance.get_list_filter(request) if list_filter is SourceAppListFilter
    )
    list_filter = source_app_filter(request, {}, Feature, admin_instance)

    assert (str(app.pk), app.display_name) in list_filter.lookups(request, admin_instance)
