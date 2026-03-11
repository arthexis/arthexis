"""Regression tests for Remote Action Token admin defaults and quick actions."""

from __future__ import annotations

import datetime
from html.parser import HTMLParser

import pytest
from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.actions.models import DashboardAction, RemoteAction, RemoteActionToken, StaffTask, StaffTaskPreference
from apps.sites.templatetags.admin_extras import model_admin_actions


pytestmark = [pytest.mark.django_db, pytest.mark.integration]

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class _LinkParser(HTMLParser):
    """Collect anchor tag attributes from rendered HTML."""

    def __init__(self):
        super().__init__()
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        self.links.append(dict(attrs))


def test_staff_task_models_use_task_panel_verbose_names():
    """Staff task admin labels should use task panel wording."""

    assert StaffTask._meta.verbose_name == "Task Panel"
    assert StaffTask._meta.verbose_name_plural == "Task Panels"
    assert StaffTaskPreference._meta.verbose_name == "Task Panel Preference"
    assert StaffTaskPreference._meta.verbose_name_plural == "Task Panel Preferences"


def test_task_panel_permission_names_are_upgraded_by_migration_function():
    """Migration helper should rename existing auth permission labels to task panel wording."""

    from django.apps import apps as global_apps
    from django.contrib.contenttypes.models import ContentType

    import importlib

    migration_module = importlib.import_module(
        "apps.actions.migrations.0007_rebrand_suite_tasks_to_task_panels"
    )

    staff_task_content_type, _ = ContentType.objects.get_or_create(
        app_label="actions",
        model="stafftask",
    )
    staff_task_preference_content_type, _ = ContentType.objects.get_or_create(
        app_label="actions",
        model="stafftaskpreference",
    )

    add_task_permission = Permission.objects.get(
        content_type=staff_task_content_type,
        codename="add_stafftask",
    )
    add_task_permission.name = "Can add Suite Task"
    add_task_permission.save(update_fields=["name"])

    view_preference_permission = Permission.objects.get(
        content_type=staff_task_preference_content_type,
        codename="view_stafftaskpreference",
    )
    view_preference_permission.name = "Can view Suite Task Preference"
    view_preference_permission.save(update_fields=["name"])

    custom_permission, _ = Permission.objects.get_or_create(
        content_type=staff_task_content_type,
        codename="approve_stafftask",
        defaults={"name": "Can approve Suite Task"},
    )

    migration_module.rename_permissions_to_task_panel_labels(global_apps, schema_editor=None)

    add_task_permission.refresh_from_db()
    view_preference_permission.refresh_from_db()
    custom_permission.refresh_from_db()

    assert add_task_permission.name == "Can add Task Panel"
    assert view_preference_permission.name == "Can view Task Panel Preference"
    assert custom_permission.name == "Can approve Suite Task"


def test_suite_task_permission_names_are_restored_by_migration_reverse_function():
    """Migration reverse helper should rename task panel permission labels back to suite task wording."""

    from django.apps import apps as global_apps
    from django.contrib.contenttypes.models import ContentType

    import importlib

    migration_module = importlib.import_module(
        "apps.actions.migrations.0007_rebrand_suite_tasks_to_task_panels"
    )

    staff_task_content_type, _ = ContentType.objects.get_or_create(
        app_label="actions",
        model="stafftask",
    )
    staff_task_preference_content_type, _ = ContentType.objects.get_or_create(
        app_label="actions",
        model="stafftaskpreference",
    )

    change_task_permission = Permission.objects.get(
        content_type=staff_task_content_type,
        codename="change_stafftask",
    )
    change_task_permission.name = "Can change Task Panel"
    change_task_permission.save(update_fields=["name"])

    delete_preference_permission = Permission.objects.get(
        content_type=staff_task_preference_content_type,
        codename="delete_stafftaskpreference",
    )
    delete_preference_permission.name = "Can delete Task Panel Preference"
    delete_preference_permission.save(update_fields=["name"])

    custom_permission, _ = Permission.objects.get_or_create(
        content_type=staff_task_content_type,
        codename="archive_stafftask",
        defaults={"name": "Can archive Task Panel"},
    )

    migration_module.rename_permissions_to_suite_task_labels(global_apps, schema_editor=None)

    change_task_permission.refresh_from_db()
    delete_preference_permission.refresh_from_db()
    custom_permission.refresh_from_db()

    assert change_task_permission.name == "Can change Suite Task"
    assert delete_preference_permission.name == "Can delete Suite Task Preference"
    assert custom_permission.name == "Can archive Task Panel"

def test_dashboard_action_rejects_recipe_with_get_method():
    """Recipe-backed actions must use POST to match the execution endpoint."""

    from django.contrib.contenttypes.models import ContentType

    from apps.recipes.models import Recipe

    content_type = ContentType.objects.get_for_model(Recipe, for_concrete_model=False)
    recipe = Recipe.objects.create(
        slug="validate-method",
        display="Validate Method",
        script="result = 'ok'",
    )
    action = DashboardAction(
        content_type=content_type,
        slug="invalid-recipe-method",
        label="Invalid Recipe Method",
        target_type=DashboardAction.TargetType.RECIPE,
        http_method=DashboardAction.HttpMethod.GET,
        recipe=recipe,
    )

    with pytest.raises(ValidationError) as exc:
        action.full_clean()

    assert "Recipe-backed actions must use POST" in str(exc.value)


def test_dashboard_action_rejects_unsafe_absolute_url():
    """Unsafe URL schemes should not be accepted for absolute-url actions."""

    from django.contrib.contenttypes.models import ContentType

    from apps.recipes.models import Recipe

    content_type = ContentType.objects.get_for_model(Recipe, for_concrete_model=False)
    action = DashboardAction(
        content_type=content_type,
        slug="unsafe-url",
        label="Unsafe",
        target_type=DashboardAction.TargetType.ABSOLUTE_URL,
        absolute_url="javascript:alert(1)",
    )

    with pytest.raises(ValidationError) as exc:
        action.full_clean()

    assert "Invalid or unsafe URL scheme" in str(exc.value)


def test_dashboard_action_execute_view_handles_recipe_failure(admin_user):
    """Recipe execution failures should show an error message instead of a 500."""

    from django.contrib.contenttypes.models import ContentType

    from apps.recipes.models import Recipe

    request = RequestFactory().post("/admin/actions/dashboardaction/1/execute/")
    request.user = admin_user
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))

    content_type = ContentType.objects.get_for_model(Recipe, for_concrete_model=False)
    recipe = Recipe.objects.create(
        slug="broken-recipe",
        display="Broken Recipe",
        script="raise RuntimeError('boom')",
    )
    action = DashboardAction.objects.create(
        content_type=content_type,
        slug="broken-recipe",
        label="Broken Recipe",
        target_type=DashboardAction.TargetType.RECIPE,
        http_method=DashboardAction.HttpMethod.POST,
        recipe=recipe,
        is_active=True,
    )

    model_admin = admin.site._registry[DashboardAction]
    response = model_admin.execute_view(request, action.pk)

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("admin:index")
    messages = [str(message) for message in request._messages]
    assert any("failed" in message.lower() for message in messages)


@pytest.mark.integration
def test_remote_action_openapi_download_requires_explicit_query_param(admin_client):
    """Regression: OpenAPI endpoint only downloads when explicitly requested."""

    response = admin_client.get(
        reverse("admin:actions_remoteaction_my_openapi_spec"),
        data={"download": "1"},
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/yaml")
    assert response.headers["Content-Disposition"] == 'attachment; filename="my-actions-openapi.yaml"'


@pytest.mark.integration
def test_remote_action_openapi_forbidden_for_unprivileged_staff(client):
    """Regression: OpenAPI preview and download require RemoteAction view/change rights."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="openapi_staff_no_remoteaction_perm",
        password="test-password",
        is_staff=True,
    )
    client.force_login(user)

    response = client.get(reverse("admin:actions_remoteaction_my_openapi_spec"))
    assert response.status_code == 403

    response = client.get(
        reverse("admin:actions_remoteaction_my_openapi_spec"),
        data={"download": "1"},
    )
    assert response.status_code == 403
