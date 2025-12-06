from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS core_totpdevicesettings;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
