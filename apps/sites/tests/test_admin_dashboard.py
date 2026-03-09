from __future__ import annotations

import pytest

from apps.counters.dashboard_rules import DEFAULT_SUCCESS_MESSAGE
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils.translation import gettext as _

@pytest.mark.django_db
def test_admin_index_hides_sidebar_widgets_for_staff_without_permissions(client):
    """Staff users without model permissions should not see dashboard widgets."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="staff-no-model-perms",
        password="unused",
        is_staff=True,
    )
    client.force_login(user)

    response = client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert _("You don't have permission to view or edit anything.") in content
    assert "id=\"admin-dashboard-widgets\"" not in content
    assert _("Configure Widgets") not in content


def test_dashboard_model_status_hides_default_success_message():
    """Default success status should render only the checkmark icon."""

    request = RequestFactory().get("/admin/")
    content = render_to_string(
        "admin/includes/dashboard_model_row.html",
        {
            "request": request,
            "model_name": "user",
            "row_dom_id": "auth-user",
            "model": {
                "admin_url": "/admin/auth/user/",
                "add_url": "/admin/auth/user/add/",
                "name": "Users",
                "object_name": "User",
            },
            "fav": None,
            "ct_id": 1,
            "show_changelinks": False,
            "show_model_badges": True,
            "model_statuses": {
                1: {
                    "success": True,
                    "icon": "✓",
                    "message": str(DEFAULT_SUCCESS_MESSAGE),
                    "is_default_message": True,
                }
            },
        },
    )

    assert "dashboard-model-status__icon" in content
    assert "✓" in content
    assert str(DEFAULT_SUCCESS_MESSAGE) not in content


def test_dashboard_model_status_keeps_failure_message_visible():
    """Failure status should continue to render the error message text."""

    request = RequestFactory().get("/admin/")
    failure_message = "Missing CP config: EVCS-1."
    content = render_to_string(
        "admin/includes/dashboard_model_row.html",
        {
            "request": request,
            "model_name": "user",
            "row_dom_id": "auth-user",
            "model": {
                "admin_url": "/admin/auth/user/",
                "add_url": "/admin/auth/user/add/",
                "name": "Users",
                "object_name": "User",
            },
            "fav": None,
            "ct_id": 1,
            "show_changelinks": False,
            "show_model_badges": True,
            "model_statuses": {
                1: {"success": False, "icon": "✗", "message": failure_message}
            },
        },
    )

    assert failure_message in content
