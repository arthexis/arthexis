from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE django_site ADD COLUMN is_seed_data BOOLEAN NOT NULL DEFAULT 0",
            reverse_sql="ALTER TABLE django_site DROP COLUMN is_seed_data",
        )
    ]
