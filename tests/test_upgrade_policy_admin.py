"""Tests for upgrade policy admin list display formatting."""

import pytest
from django.contrib import admin
from django.utils import translation

from apps.nodes.admin.upgrade_policy_admin import UpgradePolicyAdmin
from apps.nodes.models import UpgradePolicy


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (None, "-"),
        (-10, "-"),
        (0, "-"),
        (1, "1 minute"),
        (15, "15 minutes"),
        (60, "1 hour"),
        (120, "2 hours"),
        (1440, "1 day"),
        (2880, "2 days"),
    ],
)
def test_interval_display_formats(minutes, expected):
    """Render interval values as localized, human-readable units."""

    policy = UpgradePolicy(name="Policy", interval_minutes=minutes)
    model_admin = UpgradePolicyAdmin(UpgradePolicy, admin.site)

    with translation.override("en"):
        assert model_admin.interval_display(policy) == expected
