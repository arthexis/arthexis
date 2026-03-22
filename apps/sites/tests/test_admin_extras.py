"""Regression tests for admin extras action visibility."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, override_settings

from apps.actions.models import DashboardAction
from apps.sites.templatetags.admin_extras import model_admin_actions
from apps.tests.models import TestResult


class VisibilityBranchAdmin(admin.ModelAdmin):
    """Model admin exposing action branches used by dashboard row actions."""

    def get_actions(self, request):
        """Return per-user action mappings for legacy admin actions."""

        if request.user.is_superuser:
            return {
                "row_action": (self.row_action, "row_action", "Row Action"),
                "my_profile": (self.my_profile, "my_profile", "My Profile"),
                "delete_selected": (self.row_action, "delete_selected", "Delete"),
                "queryset_hidden": (
                    self.queryset_hidden,
                    "queryset_hidden",
                    "Queryset Hidden",
                ),
            }
        if request.user.is_staff:
            return {
                "row_action": (self.row_action, "row_action", "Row Action"),
            }
        return {}

    def get_changelist_actions(self, request):
        """Return per-user named changelist actions."""

        if request.user.is_superuser:
            return ["changelist_tool", "queryset_hidden"]
        if request.user.is_staff:
            return ["changelist_tool"]
        return []

    def get_dashboard_actions(self, request):
        """Return per-user dashboard actions."""

        if request.user.is_superuser:
            return ["dashboard_launch"]
        return []

    def get_my_profile_url(self, request):
        """Return the per-request my-profile URL."""

        return f"/profile/{request.user.pk}/"

    def get_my_profile_label(self, request):
        """Return the per-request my-profile label."""

        return f"Profile for {request.user.username}"

    def row_action(self, request, queryset):
        """Placeholder row action used for action visibility tests."""

    row_action.requires_queryset = False
    row_action.label = "Row Action"

    def my_profile(self, request, queryset):
        """Placeholder my-profile action used for action visibility tests."""

    my_profile.requires_queryset = False

    def queryset_hidden(self, request, queryset):
        """Placeholder queryset action hidden from row tools."""

    queryset_hidden.requires_queryset = True

    def changelist_tool(self, request):
        """Placeholder changelist tool used for action visibility tests."""

    changelist_tool.short_description = "Changelist Tool"

    def dashboard_launch(self, request):
        """Placeholder dashboard action used for action visibility tests."""

    dashboard_launch.short_description = "Dashboard Launch"
    dashboard_launch.dashboard_method = "post"
    dashboard_launch.dashboard_url = "test_dashboard_action"


class ChangelistMyProfileAdmin(admin.ModelAdmin):
    """Model admin exposing changelist-only my-profile actions."""

    def get_actions(self, request):
        """Return an empty action mapping for changelist-only coverage."""

        return {}

    def get_changelist_actions(self, request):
        """Return the changelist my-profile action for all users."""

        return ["my_profile"]

    def get_my_profile_url(self, request):
        """Return the direct profile fallback URL for the request."""

        return f"/profile-direct/{request.user.pk}/"

    def my_profile(self, request):
        """Placeholder changelist my-profile tool for routing tests."""

    my_profile.short_description = "Active Profile"


@pytest.fixture
def visibility_admin():
    """Swap in a temporary admin registration used by action visibility tests."""

    original_admin = admin.site._registry[TestResult]
    test_admin = VisibilityBranchAdmin(TestResult, admin.site)
    test_admin.tools_view_name = "test_admin_action_tool"
    admin.site._registry[TestResult] = test_admin
    try:
        yield test_admin
    finally:
        admin.site._registry[TestResult] = original_admin


@pytest.fixture
def admin_action_users(db):
    """Create superuser, staff, and restricted users for visibility checks."""

    user_model = get_user_model()
    return {
        "superuser": user_model.objects.create_superuser(
            username="admin-actions-super",
            email="super@example.com",
            password="password",
        ),
        "staff": user_model.objects.create_user(
            username="admin-actions-staff",
            email="staff@example.com",
            password="password",
            is_staff=True,
        ),
        "restricted": user_model.objects.create_user(
            username="admin-actions-restricted",
            email="restricted@example.com",
            password="password",
        ),
    }


@pytest.fixture
def request_factory():
    """Return a request factory for admin extras tests."""

    return RequestFactory()


@pytest.fixture
def changelist_my_profile_admin():
    """Swap in a temporary admin registration for changelist my-profile checks."""

    original_admin = admin.site._registry[TestResult]
    test_admin = ChangelistMyProfileAdmin(TestResult, admin.site)
    test_admin.tools_view_name = "test_admin_action_tool"
    admin.site._registry[TestResult] = test_admin
    try:
        yield test_admin
    finally:
        admin.site._registry[TestResult] = original_admin


@override_settings(ROOT_URLCONF="apps.sites.tests.urls_admin_extras")
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role", "expected_labels", "expected_methods"),
    [
        (
            "superuser",
            [
                "Configured Action",
                "Row Action",
                "Profile for admin-actions-super",
                "Changelist Tool",
                "Dashboard Launch",
            ],
            {
                "Configured Action": "get",
                "Row Action": "get",
                "Profile for admin-actions-super": "get",
                "Changelist Tool": "get",
                "Dashboard Launch": "post",
            },
        ),
        (
            "staff",
            ["Configured Action", "Row Action", "Changelist Tool"],
            {
                "Configured Action": "get",
                "Row Action": "get",
                "Changelist Tool": "get",
            },
        ),
        (
            "restricted",
            ["Configured Action"],
            {"Configured Action": "get"},
        ),
    ],
)
def test_model_admin_actions_visibility_by_role(
    visibility_admin,
    admin_action_users,
    request_factory,
    role,
    expected_labels,
    expected_methods,
):
    """Dashboard row actions should preserve role-specific visibility branches."""

    del visibility_admin
    content_type = ContentType.objects.get_for_model(TestResult, for_concrete_model=False)
    DashboardAction.objects.create(
        content_type=content_type,
        slug="configured-action",
        label="Configured Action",
        action_name="groups",
        is_active=True,
    )
    request = request_factory.get("/admin/tests/testresult/")
    request.user = admin_action_users[role]

    actions = model_admin_actions({"request": request}, "tests", "TestResult")

    assert [action["label"] for action in actions] == expected_labels
    assert {action["label"]: action["method"] for action in actions} == expected_methods
    assert all(action["url"] for action in actions)
    assert "Queryset Hidden" not in {action["label"] for action in actions}
    assert "Delete" not in {action["label"] for action in actions}

    if role == "superuser":
        assert actions[0]["url"] == "/actions/api/v1/security-groups/"
        assert actions[2]["url"] == f"/profile/{request.user.pk}/"
        assert actions[3]["url"].endswith("/test/tools/changelist_tool/")
        assert actions[4]["url"].endswith("/test/dashboard-action/")
    elif role == "staff":
        assert actions[0]["url"] == "/actions/api/v1/security-groups/"
        assert actions[2]["url"].endswith("/test/tools/changelist_tool/")


@override_settings(ROOT_URLCONF="apps.sites.tests.urls_admin_extras")
@pytest.mark.django_db
def test_model_admin_actions_routes_changelist_my_profile_through_tools(
    changelist_my_profile_admin,
    admin_action_users,
    request_factory,
):
    """Changelist my-profile actions should preserve tool-view redirects."""

    del changelist_my_profile_admin
    request = request_factory.get("/admin/tests/testresult/")
    request.user = admin_action_users["superuser"]

    actions = model_admin_actions({"request": request}, "tests", "TestResult")

    assert [action["label"] for action in actions] == ["Active Profile"]
    assert actions[0]["url"].endswith("/test/tools/my_profile/")
