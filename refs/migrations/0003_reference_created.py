from django.db import migrations, models
from django.db import connection
from django.utils import timezone


def rename_table_and_migrations(apps, schema_editor):
    with connection.cursor() as cursor:
        tables = schema_editor.connection.introspection.table_names()
        if 'references_reference' in tables:
            cursor.execute('ALTER TABLE references_reference RENAME TO refs_reference')
            cursor.execute("UPDATE django_migrations SET app='refs' WHERE app='references'")


class Migration(migrations.Migration):

    dependencies = [
        ('refs', '0002_remove_reference_is_seed_data'),
    ]

    operations = [
        migrations.RunPython(rename_table_and_migrations, migrations.RunPython.noop),
        migrations.AddField(
            model_name='reference',
            name='created',
            field=models.DateTimeField(auto_now_add=True, default=timezone.now),
            preserve_default=False,
        ),
    ]
