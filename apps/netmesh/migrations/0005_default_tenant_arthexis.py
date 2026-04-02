import json

from django.db import migrations


DEFAULT_TENANT = "arthexis"
STATE_TABLE = "netmesh_migration_0005_state"
STATE_KEY = "blank_to_default_ids"


def _ensure_state_table(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
                state_key VARCHAR(255) PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """
        )


def _store_state(schema_editor, payload):
    _ensure_state_table(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {STATE_TABLE} WHERE state_key = %s", [STATE_KEY])
        cursor.execute(
            f"INSERT INTO {STATE_TABLE} (state_key, payload) VALUES (%s, %s)",
            [STATE_KEY, json.dumps(payload, sort_keys=True)],
        )


def _load_state(schema_editor):
    _ensure_state_table(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT payload FROM {STATE_TABLE} WHERE state_key = %s", [STATE_KEY])
        row = cursor.fetchone()
    if row is None:
        return {}
    return json.loads(row[0])


def _clear_state(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {STATE_TABLE} WHERE state_key = %s", [STATE_KEY])


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

    membership_ids_to_update = list(memberships.filter(tenant="").values_list("id", flat=True))
    policy_ids_to_update = list(policies.filter(tenant="").values_list("id", flat=True))

    if membership_ids_to_update:
        memberships.filter(id__in=membership_ids_to_update).update(tenant=DEFAULT_TENANT)
    if policy_ids_to_update:
        policies.filter(id__in=policy_ids_to_update).update(tenant=DEFAULT_TENANT)

    _store_state(
        schema_editor,
        {"membership_ids": membership_ids_to_update, "policy_ids": policy_ids_to_update},
    )


def migrate_default_tenant_to_blank(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    mesh_membership = apps.get_model("netmesh", "MeshMembership")
    peer_policy = apps.get_model("netmesh", "PeerPolicy")
    memberships = mesh_membership.objects.using(db_alias)
    policies = peer_policy.objects.using(db_alias)

    state = _load_state(schema_editor)
    membership_ids = state.get("membership_ids", [])
    policy_ids = state.get("policy_ids", [])

    if membership_ids:
        memberships.filter(id__in=membership_ids, tenant=DEFAULT_TENANT).update(tenant="")
    if policy_ids:
        policies.filter(id__in=policy_ids, tenant=DEFAULT_TENANT).update(tenant="")

    _clear_state(schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("netmesh", "0004_remove_peerpolicy_netmesh_policy_source_selector_xor_and_more"),
    ]

    operations = [
        migrations.RunPython(
            migrate_blank_tenants_to_default,
            migrate_default_tenant_to_blank,
        ),
    ]
