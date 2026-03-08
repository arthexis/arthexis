"""Tests for upgrade policy admin list display formatting."""

from django.contrib import admin

from apps.nodes.admin.upgrade_policy_admin import UpgradePolicyAdmin
from apps.nodes.models import UpgradePolicy


def test_interval_display_formats_minutes():
    """Render non-hour intervals as minutes."""

    policy = UpgradePolicy(name="Minute", interval_minutes=15)
    model_admin = UpgradePolicyAdmin(UpgradePolicy, admin.site)

    assert model_admin.interval_display(policy) == "15 minutes"


def test_interval_display_formats_hours():
    """Render exact hour intervals as hours."""

    policy = UpgradePolicy(name="Hour", interval_minutes=120)
    model_admin = UpgradePolicyAdmin(UpgradePolicy, admin.site)

    assert model_admin.interval_display(policy) == "2 hours"


def test_interval_display_formats_days():
    """Render exact day intervals as days."""

    policy = UpgradePolicy(name="Day", interval_minutes=2880)
    model_admin = UpgradePolicyAdmin(UpgradePolicy, admin.site)

    assert model_admin.interval_display(policy) == "2 days"
