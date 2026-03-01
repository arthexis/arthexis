import pytest

from apps.nodes.models import Node, NodeUpgradePolicyAssignment, UpgradePolicy
from apps.nodes.tasks import apply_upgrade_policies



@pytest.mark.django_db
def test_apply_upgrade_policies_skips_inactive_assignments(monkeypatch):
    local = Node.objects.create(
        hostname="local-upgrade-node",
        current_relation=Node.Relation.SELF,
    )
    inactive = UpgradePolicy.objects.create(
        name="Inactive First",
        channel=UpgradePolicy.Channel.STABLE,
        interval_minutes=5,
        is_active=False,
    )
    active = UpgradePolicy.objects.create(
        name="Active Later",
        channel=UpgradePolicy.Channel.UNSTABLE,
        interval_minutes=30,
        is_active=True,
    )
    NodeUpgradePolicyAssignment.objects.create(node=local, policy=inactive)
    NodeUpgradePolicyAssignment.objects.create(node=local, policy=active)

    calls: list[int] = []

    def fake_check_github_updates(*, channel_override=None, policy_id=None, **kwargs):
        del channel_override, kwargs
        calls.append(policy_id)
        return "NO-UPDATES"

    monkeypatch.setattr("apps.nodes.tasks.check_github_updates", fake_check_github_updates)

    result = apply_upgrade_policies()

    assert result == "Active Later:NO-UPDATES"
    assert calls == [active.pk]
