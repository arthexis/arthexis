from django.db import migrations, models


RECIPE_DASHBOARD_ACTION_ARCHIVE_TABLE = "actions_recipe_dashboardaction_archive"


def create_recipe_dashboard_action_archive(apps, schema_editor):
    """Create a backup table for recipe-backed dashboard actions removed in this migration."""

    schema_editor.execute(
        f"""
        CREATE TABLE {RECIPE_DASHBOARD_ACTION_ARCHIVE_TABLE} (
            id bigint PRIMARY KEY,
            content_type_id bigint NOT NULL,
            slug varchar(100) NOT NULL,
            label varchar(120) NOT NULL,
            http_method varchar(8) NOT NULL,
            target_type varchar(24) NOT NULL,
            admin_url_name varchar(200) NOT NULL,
            absolute_url varchar(500) NOT NULL,
            archived_recipe_id bigint NULL,
            caller_sigil varchar(120) NOT NULL,
            is_active bool NOT NULL,
            "order" integer NOT NULL
        )
        """
    )


def drop_recipe_dashboard_action_archive(apps, schema_editor):
    """Remove the backup table used for reversible recipe dashboard action deletion."""

    schema_editor.execute(f"DROP TABLE {RECIPE_DASHBOARD_ACTION_ARCHIVE_TABLE}")


def archive_recipe_dashboard_actions(apps, schema_editor):
    """Copy recipe-backed dashboard actions into the archive table, then delete them."""

    schema_editor.execute(
        f"""
        INSERT INTO {RECIPE_DASHBOARD_ACTION_ARCHIVE_TABLE} (
            id,
            content_type_id,
            slug,
            label,
            http_method,
            target_type,
            admin_url_name,
            absolute_url,
            archived_recipe_id,
            caller_sigil,
            is_active,
            "order"
        )
        SELECT
            id,
            content_type_id,
            slug,
            label,
            http_method,
            target_type,
            admin_url_name,
            absolute_url,
            recipe_id,
            caller_sigil,
            is_active,
            "order"
        FROM actions_dashboardaction
        WHERE target_type = 'recipe'
        """
    )
    schema_editor.execute("DELETE FROM actions_dashboardaction WHERE target_type = 'recipe'")


def restore_recipe_dashboard_actions(apps, schema_editor):
    """Recreate recipe-backed dashboard actions from the archive table during rollback."""

    schema_editor.execute(
        f"""
        INSERT INTO actions_dashboardaction (
            id,
            content_type_id,
            slug,
            label,
            http_method,
            target_type,
            admin_url_name,
            absolute_url,
            recipe_id,
            caller_sigil,
            is_active,
            "order"
        )
        SELECT
            id,
            content_type_id,
            slug,
            label,
            http_method,
            target_type,
            admin_url_name,
            absolute_url,
            archived_recipe_id,
            caller_sigil,
            is_active,
            "order"
        FROM {RECIPE_DASHBOARD_ACTION_ARCHIVE_TABLE}
        """
    )


def rename_dashboardaction_recipe_to_archive(apps, schema_editor):
    """Rename the dashboard action recipe column so rollback can restore its values losslessly."""

    schema_editor.execute("ALTER TABLE actions_dashboardaction RENAME COLUMN recipe_id TO archived_recipe_id")


def restore_dashboardaction_recipe_column(apps, schema_editor):
    """Restore the original dashboard action recipe column name during rollback."""

    schema_editor.execute("ALTER TABLE actions_dashboardaction RENAME COLUMN archived_recipe_id TO recipe_id")


def rename_remoteaction_recipe_to_archive(apps, schema_editor):
    """Rename the remote action recipe column so rollback can restore required foreign keys."""

    schema_editor.execute("ALTER TABLE actions_remoteaction RENAME COLUMN recipe_id TO archived_recipe_id")


def restore_remoteaction_recipe_column(apps, schema_editor):
    """Restore the original remote action recipe column name during rollback."""

    schema_editor.execute("ALTER TABLE actions_remoteaction RENAME COLUMN archived_recipe_id TO recipe_id")


class Migration(migrations.Migration):

    dependencies = [
        ("actions", "0007_rebrand_suite_tasks_to_task_panels"),
    ]

    operations = [
        migrations.RunPython(
            create_recipe_dashboard_action_archive,
            drop_recipe_dashboard_action_archive,
        ),
        migrations.RunPython(
            archive_recipe_dashboard_actions,
            restore_recipe_dashboard_actions,
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    rename_dashboardaction_recipe_to_archive,
                    restore_dashboardaction_recipe_column,
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="dashboardaction",
                    name="recipe",
                )
            ],
        ),
        migrations.AlterField(
            model_name="dashboardaction",
            name="target_type",
            field=models.CharField(
                choices=[("admin_url", "Admin URL Name"), ("absolute_url", "Absolute URL")],
                default="admin_url",
                max_length=24,
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    rename_remoteaction_recipe_to_archive,
                    restore_remoteaction_recipe_column,
                )
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="remoteaction",
                    name="recipe",
                )
            ],
        ),
    ]
