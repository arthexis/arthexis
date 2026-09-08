from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib import admin
from django.urls import resolve, reverse

from apps.analytics.views import usage_analytics_summary
from apps.cards.auth_views import rfid_login
from apps.cards.core_views import rfid_batch
from apps.cards.models import RFID
from apps.release.views import release_progress
from apps.users.admin_views import request_temp_password, stop_impersonation
from apps.users.models import User


@pytest.mark.parametrize(
    ("legacy_module", "owner_module"),
    [
        ("apps.core.views.auth", "apps.cards.auth_views"),
        ("apps.core.views.rfid", "apps.cards.core_views"),
        ("apps.core.views.usage_analytics", "apps.analytics.views"),
        ("apps.core.admin.rfid", "apps.cards.admin_rfid"),
        ("apps.core.admin.rfid_forms", "apps.cards.rfid_forms"),
        ("apps.core.admin.usage", "apps.analytics.admin"),
        ("apps.core.admin.users", "apps.users.admin_core"),
        (
            "apps.core.views.reports.common",
            "apps.reports.views.common",
        ),
        (
            "apps.core.views.reports.logs",
            "apps.reports.views.logs",
        ),
        (
            "apps.core.views.reports.report_rendering",
            "apps.reports.views.report_rendering",
        ),
        (
            "apps.core.views.reports.release_publish.views",
            "apps.release.views",
        ),
    ],
)
def test_legacy_domain_ui_modules_are_owner_aliases(legacy_module, owner_module):
    assert importlib.import_module(legacy_module) is importlib.import_module(owner_module)


def test_odoo_legacy_modules_are_owner_aliases_when_installed():
    if not django_apps.is_installed("apps.odoo"):
        pytest.skip("Odoo app is not installed")

    aliases = [
        ("apps.core.views.odoo", "apps.odoo.core_views"),
        ("apps.core.services.odoo_quote_report", "apps.odoo.quote_report"),
        ("apps.core.admin.odoo", "apps.odoo.admin_core"),
    ]
    for legacy_module, owner_module in aliases:
        assert importlib.import_module(legacy_module) is importlib.import_module(owner_module)


def test_domain_admin_classes_are_registered_from_owner_apps():
    from apps.cards.admin_rfid import RFIDAdmin
    from apps.users.admin_core import UserAdmin

    user_admin = admin.site._registry[User]
    rfid_admin = admin.site._registry[RFID]

    # The registry contract is behavioral: Django stores ModelAdmin instances.
    # Assert that those instances derive from the canonical owner classes, then
    # inspect the canonical classes themselves for source ownership. The runtime
    # instance class can be an implementation wrapper and its __module__ is not
    # a stable ownership contract.
    assert isinstance(user_admin, UserAdmin)
    assert isinstance(rfid_admin, RFIDAdmin)
    assert UserAdmin.__module__ == "apps.users.admin_core"
    assert RFIDAdmin.__module__ == "apps.cards.admin_rfid"

    if django_apps.is_installed("apps.odoo"):
        from apps.odoo.admin_core import OdooEmployeeAdmin, OdooProductAdmin
        from apps.odoo.models import OdooEmployee, OdooProduct

        assert isinstance(admin.site._registry[OdooEmployee], OdooEmployeeAdmin)
        assert isinstance(admin.site._registry[OdooProduct], OdooProductAdmin)
        assert OdooEmployeeAdmin.__module__ == "apps.odoo.admin_core"
        assert OdooProductAdmin.__module__ == "apps.odoo.admin_core"


def test_legacy_urls_resolve_to_owner_views():
    assert reverse("rfid-login") == "/core/rfid-login/"
    assert resolve(reverse("rfid-login")).func is rfid_login

    assert reverse("rfid-batch") == "/core/rfids/"
    assert resolve(reverse("rfid-batch")).func is rfid_batch

    assert reverse("stop-impersonation") == "/core/impersonation/stop/"
    assert resolve(reverse("stop-impersonation")).func is stop_impersonation

    temp_password_url = reverse("admin-request-temp-password")
    assert temp_password_url.endswith("/request-temp-password/")
    assert resolve(temp_password_url).func is request_temp_password

    release_url = reverse("release-progress", kwargs={"pk": 7, "action": "publish"})
    assert release_url.endswith("/core/releases/7/publish/")
    assert resolve(release_url).func is release_progress

    analytics_url = reverse("usage-analytics-summary")
    assert analytics_url == "/core/usage-analytics/summary/"
    assert resolve(analytics_url).func is usage_analytics_summary


def test_odoo_legacy_urls_resolve_to_odoo_views_when_installed():
    if not django_apps.is_installed("apps.odoo"):
        pytest.skip("Odoo app is not installed")

    from apps.odoo import core_views

    expected = {
        "product-list": ("/core/products/", core_views.product_list),
        "add-live-subscription": (
            "/core/live-subscribe/",
            core_views.add_live_subscription,
        ),
        "live-subscription-list": (
            "/core/live-list/",
            core_views.live_subscription_list,
        ),
    }
    for route_name, (expected_path, expected_view) in expected.items():
        url = reverse(route_name)
        assert url == expected_path
        assert resolve(url).func is expected_view

    for route_name, expected_view in (
        ("odoo-products", core_views.odoo_products),
        ("odoo-quote-report", core_views.odoo_quote_report),
    ):
        url = reverse(route_name)
        assert resolve(url).func is expected_view


def test_domain_route_providers_are_explicit_and_core_is_narrow():
    assert "apps.analytics.routes" in settings.ROUTE_PROVIDERS
    assert "apps.release.routes" in settings.ROUTE_PROVIDERS
    assert "apps.users.routes" in settings.ROUTE_PROVIDERS

    from apps.core.routes import ROOT_URLPATTERNS

    assert {pattern.name for pattern in ROOT_URLPATTERNS} == {"version-info"}
