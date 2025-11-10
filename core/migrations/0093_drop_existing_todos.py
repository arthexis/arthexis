from django.db import migrations


def drop_existing_todos(apps, schema_editor):
    Todo = apps.get_model("core", "Todo")
    manager = getattr(Todo, "all_objects", Todo._base_manager)
    for todo in manager.all():
        todo.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0092_todo_stale_tracking"),
    ]

    operations = [
        migrations.RunPython(drop_existing_todos, migrations.RunPython.noop),
    ]
