from django.db import migrations, models


def seed_upgrade_policies(apps, schema_editor):
    UpgradePolicy = apps.get_model("nodes", "UpgradePolicy")
    NodeRole = apps.get_model("nodes", "NodeRole")
    Node = apps.get_model("nodes", "Node")
    NodeUpgradePolicyAssignment = apps.get_model(
        "nodes", "NodeUpgradePolicyAssignment"
    )

    def upsert_policy(name: str, **kwargs):
        obj, created = UpgradePolicy.objects.get_or_create(
            name=name, defaults=kwargs
        )
        if created:
            return obj
        update_fields = []
        for field, value in kwargs.items():
            if getattr(obj, field) != value:
                setattr(obj, field, value)
                update_fields.append(field)
        if update_fields:
            obj.save(update_fields=update_fields)
        return obj

    stable = upsert_policy(
        "Stable",
        description="Stable release upgrades on the standard cadence.",
        channel="stable",
        interval_minutes=10080,
        requires_canaries=False,
        requires_pypi_packages=False,
        is_seed_data=True,
    )
    unstable = upsert_policy(
        "Unstable",
        description="Unstable channel for rapid rollout testing.",
        channel="unstable",
        interval_minutes=15,
        requires_canaries=False,
        requires_pypi_packages=False,
        is_seed_data=True,
    )
    fast_lane = upsert_policy(
        "Fast Lane",
        description="Stable channel with hourly checks.",
        channel="stable",
        interval_minutes=60,
        requires_canaries=False,
        requires_pypi_packages=False,
        is_seed_data=True,
    )
    lts = upsert_policy(
        "LTS",
        description="Stable long-term support with PyPI and canary requirements.",
        channel="stable",
        interval_minutes=10080,
        requires_canaries=True,
        requires_pypi_packages=True,
        is_seed_data=True,
    )

    role_defaults = {
        "Control": fast_lane,
        "Satellite": stable,
        "Watchtower": unstable,
        "Constellation": unstable,
        "Terminal": None,
    }

    for role_name, policy in role_defaults.items():
        role = NodeRole.objects.filter(name=role_name).first()
        if not role:
            continue
        default_policy_id = policy.pk if policy else None
        if role.default_upgrade_policy_id != default_policy_id:
            role.default_upgrade_policy_id = default_policy_id
            role.save(update_fields=["default_upgrade_policy"])

        if policy is None:
            continue
        nodes = Node.objects.filter(role=role)
        for node in nodes:
            if NodeUpgradePolicyAssignment.objects.filter(node=node).exists():
                continue
            NodeUpgradePolicyAssignment.objects.create(
                node=node,
                policy=policy,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0023_rename_rpi_camera_feature"),
    ]

    operations = [
        migrations.CreateModel(
            name="UpgradePolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.CharField(blank=True, max_length=200)),
                (
                    "channel",
                    models.CharField(
                        choices=[("stable", "Stable"), ("unstable", "Unstable"), ("latest", "Latest")],
                        default="stable",
                        max_length=20,
                    ),
                ),
                (
                    "interval_minutes",
                    models.PositiveIntegerField(
                        default=10080,
                        help_text="How often to check for upgrades, in minutes.",
                    ),
                ),
                (
                    "requires_canaries",
                    models.BooleanField(
                        default=False,
                        help_text="Require configured canaries to be upgraded before proceeding.",
                    ),
                ),
                (
                    "requires_pypi_packages",
                    models.BooleanField(
                        default=False,
                        help_text="Require the latest PyPI packages before upgrading.",
                    ),
                ),
            ],
            options={
                "verbose_name": "Upgrade Policy",
                "verbose_name_plural": "Upgrade Policies",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="NodeUpgradePolicyAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("last_applied_at", models.DateTimeField(blank=True, null=True)),
                ("last_status", models.CharField(blank=True, max_length=32)),
                (
                    "node",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="upgrade_policy_assignments",
                        to="nodes.node",
                    ),
                ),
                (
                    "policy",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="node_assignments",
                        to="nodes.upgradepolicy",
                    ),
                ),
            ],
            options={
                "verbose_name": "Node Upgrade Policy Assignment",
                "verbose_name_plural": "Node Upgrade Policy Assignments",
                "unique_together": {("node", "policy")},
            },
        ),
        migrations.AddField(
            model_name="noderole",
            name="default_upgrade_policy",
            field=models.ForeignKey(
                blank=True,
                help_text="Upgrade policy assigned by default to nodes with this role.",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="default_for_roles",
                to="nodes.upgradepolicy",
            ),
        ),
        migrations.AddField(
            model_name="node",
            name="upgrade_policies",
            field=models.ManyToManyField(
                blank=True,
                help_text="Upgrade policies applied to this node.",
                related_name="nodes",
                through="nodes.NodeUpgradePolicyAssignment",
                to="nodes.upgradepolicy",
            ),
        ),
        migrations.RunPython(seed_upgrade_policies, reverse_code=migrations.RunPython.noop),
    ]
