from django.db import migrations, models


DEFAULT_TENANT = "arthexis"


def migrate_blank_tenants_to_default(apps, schema_editor):
    mesh_membership = apps.get_model("netmesh", "MeshMembership")
    peer_policy = apps.get_model("netmesh", "PeerPolicy")

    scoped_memberships = (
        mesh_membership.objects.filter(tenant__in=("", DEFAULT_TENANT))
        .values("node_id", "site_id")
        .distinct()
    )
    for scope in scoped_memberships:
        scoped_default = mesh_membership.objects.filter(
            node_id=scope["node_id"],
            site_id=scope["site_id"],
            tenant=DEFAULT_TENANT,
        ).exists()
        scoped_blank = mesh_membership.objects.filter(
            node_id=scope["node_id"],
            site_id=scope["site_id"],
            tenant="",
        ).order_by("id")
        if scoped_default:
            scoped_blank.delete()
            continue
        duplicate_blank_ids = list(scoped_blank.values_list("id", flat=True)[1:])
        if duplicate_blank_ids:
            mesh_membership.objects.filter(id__in=duplicate_blank_ids).delete()

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
                blank=False,
                default=DEFAULT_TENANT,
                help_text="Tenant identifier for external mesh orchestration scope.",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="peerpolicy",
            name="tenant",
            field=models.CharField(
                blank=False,
                default=DEFAULT_TENANT,
                help_text="Tenant identifier that owns this policy.",
                max_length=64,
            ),
        ),
        migrations.AddConstraint(
            model_name="meshmembership",
            constraint=models.CheckConstraint(
                condition=~models.Q(tenant=""),
                name="netmesh_membership_tenant_non_empty",
            ),
        ),
        migrations.AddConstraint(
            model_name="peerpolicy",
            constraint=models.CheckConstraint(
                condition=~models.Q(tenant=""),
                name="netmesh_peerpolicy_tenant_non_empty",
            ),
        ),
    ]
