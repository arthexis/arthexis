from django.db import migrations, models


PROFILE_HELP_TEXT = "Optional profile providing metadata for automation tasks."


def add_profile_column(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    connection = schema_editor.connection
    table = Node._meta.db_table
    existing_columns = {
        column.name
        for column in connection.introspection.get_table_description(
            connection.cursor(), table
        )
    }
    if "profile_id" in existing_columns:
        return

    field = models.OneToOneField(
        "nodes.NodeProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="node",
        help_text=PROFILE_HELP_TEXT,
    )
    field.set_attributes_from_name("profile")
    schema_editor.add_field(Node, field)


def remove_profile_column(apps, schema_editor):
    Node = apps.get_model("nodes", "Node")
    connection = schema_editor.connection
    table = Node._meta.db_table
    existing_columns = {
        column.name
        for column in connection.introspection.get_table_description(
            connection.cursor(), table
        )
    }
    if "profile_id" not in existing_columns:
        return

    field = Node._meta.get_field("profile")
    schema_editor.remove_field(Node, field)


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0003_nodemanager_avatar"),
    ]

    operations = [
        migrations.RunPython(add_profile_column, reverse_code=remove_profile_column),
    ]
