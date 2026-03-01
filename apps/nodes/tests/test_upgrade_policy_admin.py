from django.urls import reverse

import pytest

from apps.nodes.models import UpgradePolicy



@pytest.mark.parametrize(
    ("action", "initial_active", "expected_active"),
    [
        ("activate_selected_policies", False, True),
        ("deactivate_selected_policies", True, False),
    ],
)
@pytest.mark.django_db
def test_upgrade_policy_bulk_activation_actions(
    admin_client,
    action,
    initial_active,
    expected_active,
):
    """Admin actions toggle selected upgrade policies from the changelist."""

    policy = UpgradePolicy.objects.create(
        name=f"Policy for {action}",
        channel=UpgradePolicy.Channel.STABLE,
        interval_minutes=30,
        is_active=initial_active,
    )

    response = admin_client.post(
        reverse("admin:nodes_upgradepolicy_changelist"),
        {
            "action": action,
            "_selected_action": [str(policy.pk)],
            "index": "0",
        },
        follow=True,
    )

    assert response.status_code == 200
    policy.refresh_from_db()
    assert policy.is_active is expected_active


@pytest.mark.django_db
def test_upgrade_policy_changelist_uses_short_pypi_column_label(admin_client):
    """Upgrade policy changelist renders a short column heading for PyPI requirements."""

    response = admin_client.get(reverse("admin:nodes_upgradepolicy_changelist"))

    assert response.status_code == 200
    assert "Requires PyPI" in response.content.decode()
