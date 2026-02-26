from django.urls import reverse

import pytest

from apps.nodes.models import UpgradePolicy

pytestmark = pytest.mark.critical


@pytest.mark.django_db
def test_activate_selected_upgrade_policies_action(admin_client):
    """Admin action activates selected upgrade policies from the changelist."""

    inactive = UpgradePolicy.objects.create(
        name="Inactive Policy",
        channel=UpgradePolicy.Channel.STABLE,
        interval_minutes=30,
        is_active=False,
    )

    response = admin_client.post(
        reverse("admin:nodes_upgradepolicy_changelist"),
        {
            "action": "activate_selected_policies",
            "_selected_action": [str(inactive.pk)],
            "index": "0",
        },
        follow=True,
    )

    assert response.status_code == 200
    inactive.refresh_from_db()
    assert inactive.is_active is True
