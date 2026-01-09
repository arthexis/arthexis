from django.db import migrations


def drop_nodeservice_table(apps, schema_editor):
    table_name = "nodes_nodeservice"
    connection = schema_editor.connection
    if table_name not in connection.introspection.table_names():
        return
    quoted_name = schema_editor.quote_name(table_name)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT 1 FROM {quoted_name} LIMIT 1")
        if cursor.fetchone():
            return
    schema_editor.execute(f"DROP TABLE IF EXISTS {quoted_name}")


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0014_netmessage_lcd_channel_num_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(drop_nodeservice_table, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.DeleteModel(name="NodeService"),
            ],
        ),
    ]
