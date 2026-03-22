"""Replace generic remote actions with named internal actions."""

from __future__ import annotations

from django.db import migrations, models


DASHBOARD_ACTION_ARCHIVE_TABLE = "actions_dashboardaction_archive_v2"
REMOTE_ACTION_ARCHIVE_TABLE = "actions_remoteaction_archive_v2"
REMOTE_ACTION_TOKEN_ARCHIVE_TABLE = "actions_remoteactiontoken_archive_v2"

KNOWN_ACTION_BY_ROUTE = {
    "/actions/api/v1/security-groups/": "groups",
    "admin:config": "config",
    "admin:environment": "environment",
    "admin:log_viewer": "logs",
    "admin:nodes_nodefeature_discover": "discover",
    "admin:seed_data": "seed",
    "admin:sigil_builder": "sigil",
    "admin:system": "tasks",
    "admin:system-dashboard-rules-report": "rules",
    "admin:system-details": "system",
    "admin:system-reports": "reports",
    "admin:system-upgrade-report": "upgrade",
    "admin:user_data": "data",
}


def _legacy_route_for_action_name(action_name: str) -> tuple[str, str]:
    """Return the legacy target metadata used during rollback.

    Parameters:
        action_name: Named internal action being rolled back.

    Returns:
        A ``(target_type, route)`` tuple for the legacy dashboard action fields.
    """

    for route, known_action_name in KNOWN_ACTION_BY_ROUTE.items():
        if known_action_name != action_name:
            continue
        if route.startswith("admin:"):
            return ("admin_url", route)
        return ("absolute_url", route)
    return ("admin_url", "")


def _create_archive_tables(schema_editor) -> None:
    """Create archive tables used to preserve removed generic action rows."""

    existing_tables = set(schema_editor.connection.introspection.table_names())

    class RemoteActionArchive(models.Model):
        id = models.BigIntegerField(primary_key=True)
        is_seed_data = models.BooleanField()
        is_user_data = models.BooleanField()
        is_deleted = models.BooleanField()
        uuid = models.CharField(max_length=36)
        display = models.CharField(max_length=120)
        slug = models.CharField(max_length=100)
        operation_id = models.CharField(max_length=120)
        description = models.TextField()
        is_active = models.BooleanField()
        created_at = models.DateTimeField()
        updated_at = models.DateTimeField()
        group_id = models.BigIntegerField(null=True)
        user_id = models.BigIntegerField(null=True)
        archived_at = models.DateTimeField(auto_now_add=True)

        class Meta:
            app_label = "actions"
            db_table = REMOTE_ACTION_ARCHIVE_TABLE
            managed = False

    class RemoteActionTokenArchive(models.Model):
        id = models.BigIntegerField(primary_key=True)
        label = models.CharField(max_length=100)
        key_prefix = models.CharField(max_length=12)
        key_hash = models.CharField(max_length=64)
        expires_at = models.DateTimeField()
        last_used_at = models.DateTimeField(null=True)
        is_active = models.BooleanField()
        created_at = models.DateTimeField()
        user_id = models.BigIntegerField()
        archived_at = models.DateTimeField(auto_now_add=True)

        class Meta:
            app_label = "actions"
            db_table = REMOTE_ACTION_TOKEN_ARCHIVE_TABLE
            managed = False

    class DashboardActionArchive(models.Model):
        id = models.BigIntegerField(primary_key=True)
        content_type_id = models.BigIntegerField()
        slug = models.CharField(max_length=100)
        label = models.CharField(max_length=120)
        http_method = models.CharField(max_length=8)
        target_type = models.CharField(max_length=24)
        admin_url_name = models.CharField(max_length=200)
        absolute_url = models.CharField(max_length=500)
        caller_sigil = models.CharField(max_length=120)
        is_active = models.BooleanField()
        order = models.IntegerField(db_column="order")
        archived_at = models.DateTimeField(auto_now_add=True)

        class Meta:
            app_label = "actions"
            db_table = DASHBOARD_ACTION_ARCHIVE_TABLE
            managed = False

    for archive_model in (RemoteActionArchive, RemoteActionTokenArchive, DashboardActionArchive):
        if archive_model._meta.db_table not in existing_tables:
            schema_editor.create_model(archive_model)



def _clear_archive_tables(schema_editor) -> None:
    """Remove prior archived rows so the migration can be reapplied after rollback."""

    for table_name in (
        DASHBOARD_ACTION_ARCHIVE_TABLE,
        REMOTE_ACTION_ARCHIVE_TABLE,
        REMOTE_ACTION_TOKEN_ARCHIVE_TABLE,
    ):
        schema_editor.execute(f"DELETE FROM {table_name}")


def _archive_remote_rows(schema_editor) -> None:
    """Copy generic remote action rows into archive tables."""

    _clear_archive_tables(schema_editor)

    schema_editor.execute(
        f'''
        INSERT INTO {REMOTE_ACTION_ARCHIVE_TABLE} (
            id, is_seed_data, is_user_data, is_deleted, uuid, display, slug, operation_id,
            description, is_active, created_at, updated_at, group_id, user_id
        )
        SELECT
            id, is_seed_data, is_user_data, is_deleted, uuid, display, slug, operation_id,
            description, is_active, created_at, updated_at, group_id, user_id
        FROM actions_remoteaction
        '''
    )
    schema_editor.execute(
        f'''
        INSERT INTO {REMOTE_ACTION_TOKEN_ARCHIVE_TABLE} (
            id, label, key_prefix, key_hash, expires_at, last_used_at, is_active, created_at, user_id
        )
        SELECT
            id, label, key_prefix, key_hash, expires_at, last_used_at, is_active, created_at, user_id
        FROM actions_remoteactiontoken
        '''
    )



def forward_migrate_actions(apps, schema_editor) -> None:
    """Map supported links to named actions and archive removed generic rows."""

    _create_archive_tables(schema_editor)
    _archive_remote_rows(schema_editor)

    DashboardAction = apps.get_model("actions", "DashboardAction")
    StaffTask = apps.get_model("actions", "StaffTask")

    for action in DashboardAction.objects.all():
        action_name = KNOWN_ACTION_BY_ROUTE.get(action.admin_url_name) or KNOWN_ACTION_BY_ROUTE.get(action.absolute_url)
        if action_name:
            action.action_name = action_name
            action.save(update_fields=["action_name"])
            continue
        schema_editor.execute(
            f'''
            INSERT INTO {DASHBOARD_ACTION_ARCHIVE_TABLE} (
                id, content_type_id, slug, label, http_method, target_type, admin_url_name,
                absolute_url, caller_sigil, is_active, "order"
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            [
                action.pk,
                action.content_type_id,
                action.slug,
                action.label,
                action.http_method,
                action.target_type,
                action.admin_url_name,
                action.absolute_url,
                action.caller_sigil,
                action.is_active,
                action.order,
            ],
        )
        action.delete()

    groups_task = StaffTask.objects.filter(slug="groups").first()
    legacy_actions_task = StaffTask.objects.filter(admin_url_name="admin:actions_remoteaction_my_openapi_spec").first()
    if legacy_actions_task is not None:
        if groups_task is None or groups_task.pk == legacy_actions_task.pk:
            legacy_actions_task.slug = "groups"
            legacy_actions_task.label = "Groups"
            legacy_actions_task.description = "Browse the current user's security groups."
            legacy_actions_task.action_name = "groups"
            legacy_actions_task.save(update_fields=["slug", "label", "description", "action_name"])
        else:
            legacy_actions_task.delete()

    for task in StaffTask.objects.exclude(action_name="groups"):
        action_name = KNOWN_ACTION_BY_ROUTE.get(task.admin_url_name)
        if action_name:
            task.action_name = action_name
            task.save(update_fields=["action_name"])



def reverse_migrate_actions(apps, schema_editor) -> None:
    """Restore archived generic action data during rollback."""

    DashboardAction = apps.get_model("actions", "DashboardAction")
    RemoteAction = apps.get_model("actions", "RemoteAction")
    RemoteActionToken = apps.get_model("actions", "RemoteActionToken")
    StaffTask = apps.get_model("actions", "StaffTask")

    for action in DashboardAction.objects.all():
        target_type, route = _legacy_route_for_action_name(action.action_name)
        action.admin_url_name = route if target_type == "admin_url" else ""
        action.absolute_url = route if target_type == "absolute_url" else ""
        action.http_method = "get"
        action.target_type = target_type
        action.caller_sigil = ""
        action.save(
            update_fields=[
                "admin_url_name",
                "absolute_url",
                "http_method",
                "target_type",
                "caller_sigil",
            ]
        )

    if StaffTask.objects.filter(slug="groups", action_name="groups").exists():
        groups_task = StaffTask.objects.get(slug="groups", action_name="groups")
        groups_task.slug = "actions"
        groups_task.label = "Actions"
        groups_task.description = "Open personal action OpenAPI and remote action tooling."
        groups_task.admin_url_name = "admin:actions_remoteaction_my_openapi_spec"
        groups_task.save(update_fields=["slug", "label", "description", "admin_url_name"])

    for task in StaffTask.objects.exclude(admin_url_name="admin:actions_remoteaction_my_openapi_spec"):
        target_type, route = _legacy_route_for_action_name(task.action_name)
        if target_type == "admin_url" and route:
            task.admin_url_name = route
            task.save(update_fields=["admin_url_name"])

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, is_seed_data, is_user_data, is_deleted, uuid, display, slug, operation_id, description, is_active, created_at, updated_at, group_id, user_id FROM {REMOTE_ACTION_ARCHIVE_TABLE}"
        )
        for row in cursor.fetchall():
            RemoteAction.objects.update_or_create(
                id=row[0],
                defaults={
                    "is_seed_data": row[1],
                    "is_user_data": row[2],
                    "is_deleted": row[3],
                    "uuid": row[4],
                    "display": row[5],
                    "slug": row[6],
                    "operation_id": row[7],
                    "description": row[8],
                    "is_active": row[9],
                    "created_at": row[10],
                    "updated_at": row[11],
                    "group_id": row[12],
                    "user_id": row[13],
                },
            )
        cursor.execute(
            f"SELECT id, label, key_prefix, key_hash, expires_at, last_used_at, is_active, created_at, user_id FROM {REMOTE_ACTION_TOKEN_ARCHIVE_TABLE}"
        )
        for row in cursor.fetchall():
            RemoteActionToken.objects.update_or_create(
                id=row[0],
                defaults={
                    "label": row[1],
                    "key_prefix": row[2],
                    "key_hash": row[3],
                    "expires_at": row[4],
                    "last_used_at": row[5],
                    "is_active": row[6],
                    "created_at": row[7],
                    "user_id": row[8],
                },
            )
        cursor.execute(
            f"SELECT id, content_type_id, slug, label, http_method, target_type, admin_url_name, absolute_url, caller_sigil, is_active, \"order\" FROM {DASHBOARD_ACTION_ARCHIVE_TABLE}"
        )
        for row in cursor.fetchall():
            DashboardAction.objects.update_or_create(
                id=row[0],
                defaults={
                    "content_type_id": row[1],
                    "slug": row[2],
                    "label": row[3],
                    "http_method": row[4],
                    "target_type": row[5],
                    "admin_url_name": row[6],
                    "absolute_url": row[7],
                    "caller_sigil": row[8],
                    "is_active": row[9],
                    "order": row[10],
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("actions", "0008_remove_recipe_targets"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="dashboardaction",
            options={
                "ordering": ("order", "slug"),
                "verbose_name": "Dashboard Action",
                "verbose_name_plural": "Dashboard Actions",
            },
        ),
        migrations.AddField(
            model_name="dashboardaction",
            name="action_name",
            field=models.CharField(
                choices=[
                    ("config", "Config"),
                    ("data", "Data"),
                    ("discover", "Discover"),
                    ("environment", "Environment"),
                    ("groups", "Groups"),
                    ("logs", "Logs"),
                    ("reports", "Reports"),
                    ("rules", "Rules"),
                    ("seed", "Seed"),
                    ("sigil", "Sigil"),
                    ("system", "System"),
                    ("tasks", "Tasks"),
                    ("upgrade", "Upgrade"),
                ],
                default="config",
                help_text="Named internal action rendered for this model row.",
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="stafftask",
            name="action_name",
            field=models.CharField(
                choices=[
                    ("config", "Config"),
                    ("data", "Data"),
                    ("discover", "Discover"),
                    ("environment", "Environment"),
                    ("groups", "Groups"),
                    ("logs", "Logs"),
                    ("reports", "Reports"),
                    ("rules", "Rules"),
                    ("seed", "Seed"),
                    ("sigil", "Sigil"),
                    ("system", "System"),
                    ("tasks", "Tasks"),
                    ("upgrade", "Upgrade"),
                ],
                default="config",
                max_length=50,
            ),
        ),
        migrations.RunPython(forward_migrate_actions, reverse_migrate_actions),
        migrations.AlterField(
            model_name="dashboardaction",
            name="label",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.RemoveField(
            model_name="dashboardaction",
            name="absolute_url",
        ),
        migrations.RemoveField(
            model_name="dashboardaction",
            name="admin_url_name",
        ),
        migrations.RemoveField(
            model_name="dashboardaction",
            name="caller_sigil",
        ),
        migrations.RemoveField(
            model_name="dashboardaction",
            name="http_method",
        ),
        migrations.RemoveField(
            model_name="dashboardaction",
            name="target_type",
        ),
        migrations.RemoveField(
            model_name="stafftask",
            name="admin_url_name",
        ),
        migrations.DeleteModel(
            name="RemoteAction",
        ),
        migrations.DeleteModel(
            name="RemoteActionToken",
        ),
    ]
