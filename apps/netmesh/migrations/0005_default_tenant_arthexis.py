from django.db import migrations


DEFAULT_TENANT = "arthexis"


def migrate_blank_tenants_to_default(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    mesh_membership = apps.get_model("netmesh", "MeshMembership")
    peer_policy = apps.get_model("netmesh", "PeerPolicy")
    memberships = mesh_membership.objects.using(db_alias)
    policies = peer_policy.objects.using(db_alias)

    scoped_memberships = (
        memberships.filter(tenant__in=("", DEFAULT_TENANT))
        .values("node_id", "site_id")
        .distinct()
    )
    for scope in scoped_memberships:
        scoped_default = memberships.filter(
            node_id=scope["node_id"],
            site_id=scope["site_id"],
            tenant=DEFAULT_TENANT,
        ).exists()
        scoped_blank = memberships.filter(
            node_id=scope["node_id"],
            site_id=scope["site_id"],
            tenant="",
        ).order_by("id")
        if scoped_default:
            scoped_blank.delete()
            continue
        duplicate_blank_ids = list(scoped_blank.values_list("id", flat=True)[1:])
        if duplicate_blank_ids:
            memberships.filter(id__in=duplicate_blank_ids).delete()

    memberships.filter(tenant="").update(tenant=DEFAULT_TENANT)
    policies.filter(tenant="").update(tenant=DEFAULT_TENANT)


class Migration(migrations.Migration):
    dependencies = [
        ("netmesh", "0004_remove_peerpolicy_netmesh_policy_source_selector_xor_and_more"),
    ]

    operations = [
        migrations.RunPython(
            migrate_blank_tenants_to_default,
            migrations.RunPython.noop,
        ),
    ]
