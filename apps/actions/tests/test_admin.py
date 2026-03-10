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


def test_staff_task_models_use_suite_task_verbose_names():
    """Staff task admin labels should use suite task wording."""

    assert StaffTask._meta.verbose_name == "Suite Task"
    assert StaffTask._meta.verbose_name_plural == "Suite Tasks"
    assert StaffTaskPreference._meta.verbose_name == "Suite Task Preference"
    assert StaffTaskPreference._meta.verbose_name_plural == "Suite Task Preferences"


def test_suite_task_permission_names_are_upgraded_by_migration_function():
    """Migration helper should rename existing auth permission labels to suite wording."""

    from django.apps import apps as global_apps
    from django.contrib.contenttypes.models import ContentType

    import importlib

    migration_module = importlib.import_module(
        "apps.actions.migrations.0006_alter_stafftask_options_and_more"
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
    add_task_permission.name = "Can add Staff Task"
    add_task_permission.save(update_fields=["name"])

    view_preference_permission = Permission.objects.get(
        content_type=staff_task_preference_content_type,
        codename="view_stafftaskpreference",
    )
    view_preference_permission.name = "Can view Staff Task Preference"
    view_preference_permission.save(update_fields=["name"])

    custom_permission, _ = Permission.objects.get_or_create(
        content_type=staff_task_content_type,
        codename="approve_stafftask",
        defaults={"name": "Can approve Staff Task"},
    )

    migration_module.rename_permissions_to_suite_task_labels(global_apps, schema_editor=None)

    add_task_permission.refresh_from_db()
    view_preference_permission.refresh_from_db()
    custom_permission.refresh_from_db()

    assert add_task_permission.name == "Can add Suite Task"
    assert view_preference_permission.name == "Can view Suite Task Preference"
    assert custom_permission.name == "Can approve Staff Task"



@pytest.mark.integration
def test_remote_action_token_admin_add_defaults_to_request_user(admin_client, admin_user):
    """Regression: add form defaults the owner to the logged-in admin user."""

    request = RequestFactory().get(reverse("admin:actions_remoteactiontoken_add"))
    request.user = admin_user

    model_admin = admin.site._registry[RemoteActionToken]
    initial = model_admin.get_changeform_initial_data(request)

    assert initial["user"] == request.user.pk


@pytest.mark.integration
def test_remote_action_token_admin_add_defaults_expiration_to_24h(admin_client, admin_user):
    """Regression: add form defaults expiration around 24 hours into the future."""

    request = RequestFactory().get(reverse("admin:actions_remoteactiontoken_add"))
    request.user = admin_user

    model_admin = admin.site._registry[RemoteActionToken]
    before = timezone.localtime(timezone.now() + datetime.timedelta(hours=24))
    initial = model_admin.get_changeform_initial_data(request)
    after = timezone.localtime(timezone.now() + datetime.timedelta(hours=24))

    assert before <= initial["expires_at"] <= after


def test_remote_action_token_generate_tool_creates_token_for_current_user(admin_client, admin_user):
    """Regression: one-click generate tool issues a token for current user and redirects."""

    user = admin_user

    with override_settings(STORAGES=TEST_STORAGES):
        response = admin_client.get(reverse("admin:actions_remoteactiontoken_generate_token"), follow=True)

    assert response.status_code == 200
    assert RemoteActionToken.objects.filter(user=user).exists()


def test_remote_action_token_dashboard_includes_generate_action_link(admin_client):
    """Regression: token model exposes Generate Token as a row action, not a top button."""

    with override_settings(STORAGES=TEST_STORAGES):
        response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    action_url = reverse("admin:actions_remoteactiontoken_generate_token")

    actions = model_admin_actions({"request": response.wsgi_request}, "actions", "RemoteActionToken")

    assert any(action["url"] == action_url for action in actions)

    parser = _LinkParser()
    parser.feed(response.content.decode())
    assert not any(
        link.get("href") == action_url and "button" in link.get("class", "").split()
        for link in parser.links
    )


def test_remote_action_token_dashboard_shows_generate_link_for_add_only_admin(client):
    """Regression: dashboard keeps Generate Token visible for add-only users."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="token_dashboard_add_only",
        password="test-password",
        is_staff=True,
    )
    add_permission = Permission.objects.get(codename="add_remoteactiontoken")
    user.user_permissions.add(add_permission)
    client.force_login(user)

    with override_settings(STORAGES=TEST_STORAGES):
        response = client.get(reverse("admin:index"))

    assert response.status_code == 200
    action_url = reverse("admin:actions_remoteactiontoken_generate_token")

    parser = _LinkParser()
    parser.feed(response.content.decode())
    assert any(link.get("href") == action_url for link in parser.links)


@pytest.mark.integration
def test_remote_action_token_generate_tool_redirects_to_add_when_list_inaccessible(client):
    """Regression: quick generator redirects to add page when changelist is not viewable."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="token_creator_only",
        password="test-password",
        is_staff=True,
    )
    add_permission = Permission.objects.get(codename="add_remoteactiontoken")
    user.user_permissions.add(add_permission)
    client.force_login(user)

    response = client.get(reverse("admin:actions_remoteactiontoken_generate_token"), follow=False)

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("admin:actions_remoteactiontoken_add")


def test_remote_action_dashboard_button_opens_preview_page(admin_user):
    """Regression: dashboard Actions button opens an OpenAPI preview page first."""

    request = RequestFactory().get(reverse("admin:actions_remoteaction_my_openapi_spec"))
    request.user = admin_user

    remote_action_admin = admin.site._registry[RemoteAction]

    response = remote_action_admin.my_openapi_spec_view(request)

    assert response.status_code == 200
    assert response.context_data["download_url"].endswith("?download=1")
    assert response.context_data["actions_changelist_url"] == reverse("admin:actions_remoteaction_changelist")
    assert "openapi" in response.context_data["payload"].lower()


def test_model_admin_actions_includes_declarative_dashboard_action(admin_client):
    """Dashboard rows should include declarative actions from DashboardAction records."""

    from django.contrib.contenttypes.models import ContentType

    from apps.recipes.models import Recipe

    content_type = ContentType.objects.get_for_model(Recipe, for_concrete_model=False)
    DashboardAction.objects.create(
        content_type=content_type,
        slug="recipe-bulk-import",
        label="Bulk Import",
        target_type=DashboardAction.TargetType.ABSOLUTE_URL,
        absolute_url="/admin/recipes/recipe/bulk-import/",
        http_method=DashboardAction.HttpMethod.GET,
    )

    response = admin_client.get(reverse("admin:index"))
    assert response.status_code == 200

    actions = model_admin_actions({"request": response.wsgi_request}, "recipes", "Recipe")

    assert any(item["url"].endswith("bulk-import/") and item["method"] == "get" for item in actions)


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
