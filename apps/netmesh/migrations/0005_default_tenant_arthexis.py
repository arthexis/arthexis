from django.db import migrations, models


DEFAULT_TENANT = "arthexis"


def migrate_blank_tenants_to_default(apps, schema_editor):
    mesh_membership = apps.get_model("netmesh", "MeshMembership")
    peer_policy = apps.get_model("netmesh", "PeerPolicy")
    mesh_membership.objects.filter(tenant="").update(tenant=DEFAULT_TENANT)
    peer_policy.objects.filter(tenant="").update(tenant=DEFAULT_TENANT)


class Migration(migrations.Migration):
    dependencies = [
        ("netmesh", "0004_remove_peerpolicy_netmesh_policy_source_selector_xor_and_more"),
    ]

    operations = [
        migrations.RunPython(
            migrate_blank_tenants_to_default,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="meshmembership",
            name="tenant",
            field=models.CharField(
                blank=True,
                default=DEFAULT_TENANT,
                help_text="Tenant identifier for external mesh orchestration scope.",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="peerpolicy",
            name="tenant",
            field=models.CharField(
                blank=True,
                default=DEFAULT_TENANT,
                help_text="Tenant identifier that owns this policy.",
                max_length=64,
            ),
        ),
    ]
