"""Regression tests for admin app list ordering by configured application priority."""

from django.contrib import admin
from django.test import RequestFactory

from apps.app.models import Application
from apps.core.admin.site import get_app_list_with_protocol_forwarder

import pytest

pytestmark = pytest.mark.django_db


class _User:
    """Simple authenticated user stub for admin list requests."""

    is_active = True
    is_staff = True
    is_superuser = True

    def has_perm(self, perm):
        """Return True for all permission checks used in admin list rendering."""
        return True



def _build_request():
    """Build an admin-like request with a privileged user."""
    request = RequestFactory().get("/admin/")
    request.user = _User()
    return request



def test_admin_app_list_prioritizes_configured_applications(monkeypatch):
    """Configured applications are shown before unconfigured entries in ascending priority order."""
    Application.objects.create(name="alphaapp", order=20)
    Application.objects.create(name="betaapp", order=10)

    def fake_get_app_list(self, request, app_label=None):
        return [
            {"app_label": "sites", "name": "Sites", "models": []},
            {"app_label": "alphaapp", "name": "Alphaapp", "models": []},
            {"app_label": "betaapp", "name": "Betaapp", "models": []},
        ]

    monkeypatch.setattr("apps.core.admin.site._original_admin_get_app_list", fake_get_app_list)

    result = get_app_list_with_protocol_forwarder(admin.site, _build_request())

    assert [entry["app_label"] for entry in result] == ["betaapp", "alphaapp", "sites"]



def test_admin_app_list_disambiguates_matching_priorities(monkeypatch):
    """Apps sharing a priority receive alphabetical suffixes to disambiguate ordering."""
    Application.objects.create(name="alphaapp", order=10)
    Application.objects.create(name="betaapp", order=10)

    def fake_get_app_list(self, request, app_label=None):
        return [
            {"app_label": "alphaapp", "name": "Alphaapp", "models": []},
            {"app_label": "betaapp", "name": "Betaapp", "models": []},
        ]

    monkeypatch.setattr("apps.core.admin.site._original_admin_get_app_list", fake_get_app_list)

    result = get_app_list_with_protocol_forwarder(admin.site, _build_request())

    assert [entry["name"] for entry in result] == ["10a. alphaapp", "10b. betaapp"]
