from django.db import migrations


ROLE_DEFAULTS = {
    "Control": "Stable",
    "Watchtower": "Stable",
    "Terminal": "Stable",
}


def set_role_default_upgrade_policies(apps, schema_editor):
    """Set conservative stable defaults for primary node roles."""

    del schema_editor
    NodeRole = apps.get_model("nodes", "NodeRole")
    UpgradePolicy = apps.get_model("nodes", "UpgradePolicy")

    for role_name, policy_name in ROLE_DEFAULTS.items():
        role = NodeRole.objects.filter(name=role_name).first()
        policy = UpgradePolicy.objects.filter(name=policy_name).first()
        if role and policy:
            role.default_upgrade_policy = policy
            role.save(update_fields=["default_upgrade_policy"])


def unset_role_default_upgrade_policies(apps, schema_editor):
    """Restore prior role-specific upgrade policy defaults."""

    del schema_editor
    NodeRole = apps.get_model("nodes", "NodeRole")
    UpgradePolicy = apps.get_model("nodes", "UpgradePolicy")

    control = NodeRole.objects.filter(name="Control").first()
    fast_lane = UpgradePolicy.objects.filter(name="Fast Lane").first()
    if control:
        control.default_upgrade_policy = fast_lane
        control.save(update_fields=["default_upgrade_policy"])

    watchtower = NodeRole.objects.filter(name="Watchtower").first()
    unstable = UpgradePolicy.objects.filter(name="Unstable").first()
    if watchtower:
        watchtower.default_upgrade_policy = unstable
        watchtower.save(update_fields=["default_upgrade_policy"])

    terminal = NodeRole.objects.filter(name="Terminal").first()
    if terminal:
        terminal.default_upgrade_policy = None
        terminal.save(update_fields=["default_upgrade_policy"])


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0040_merge_20260307_2002"),
    ]

    operations = [
        migrations.RunPython(
            set_role_default_upgrade_policies,
            unset_role_default_upgrade_policies,
        ),
    ]
