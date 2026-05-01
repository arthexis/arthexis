"""Regression tests for Evergo order admin permission surfaces."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from apps.evergo.admin import EvergoOrderAdmin
from apps.evergo.models import EvergoOrder
from apps.groups.models import SecurityGroup


def _admin_request(user):
    request = RequestFactory().get("/admin/evergo/evergoorder/")
    request.user = user
    return request


@pytest.mark.django_db
def test_order_admin_hides_process_flow_for_staff_without_evergo_group():
    user_model = get_user_model()
    staff = user_model.objects.create_user(
        username="evergo-staff-no-sg",
        email="evergo-staff-no-sg@example.com",
        is_staff=True,
    )
    request = _admin_request(staff)
    admin_instance = EvergoOrderAdmin(EvergoOrder, admin.site)

    assert "status_name_link" not in admin_instance.get_list_display(request)
    assert "status_name" in admin_instance.get_list_display(request)
    assert "evergo_flow_link" not in admin_instance.get_readonly_fields(request)
    fieldset_fields = [
        field
        for _title, options in admin_instance.get_fieldsets(request)
        for field in options["fields"]
    ]
    assert "evergo_flow_link" not in fieldset_fields
    assert "process_so_action" not in admin_instance.get_change_actions(request, "1", "")

    with pytest.raises(PermissionDenied):
        admin_instance.process_so_action(request, EvergoOrder(remote_id=9912))


@pytest.mark.django_db
def test_order_admin_shows_process_flow_for_evergo_group_member():
    user_model = get_user_model()
    staff = user_model.objects.create_user(
        username="evergo-staff-with-sg",
        email="evergo-staff-with-sg@example.com",
        is_staff=True,
    )
    group = SecurityGroup.objects.create(name="Evergo Contractors")
    staff.groups.add(group)
    request = _admin_request(staff)
    admin_instance = EvergoOrderAdmin(EvergoOrder, admin.site)

    assert "status_name_link" in admin_instance.get_list_display(request)
    assert "evergo_flow_link" in admin_instance.get_readonly_fields(request)
    assert "process_so_action" in admin_instance.get_change_actions(request, "1", "")
