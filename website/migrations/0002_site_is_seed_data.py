from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0001_initial"),
        ("sites", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE django_site ADD COLUMN is_seed_data BOOLEAN NOT NULL DEFAULT 0",
            reverse_sql="ALTER TABLE django_site DROP COLUMN is_seed_data",
        )
    ]

