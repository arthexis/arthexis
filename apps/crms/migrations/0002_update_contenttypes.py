from django.db import migrations


def forwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    for model in ("product", "odooprofile"):
        ContentType.objects.filter(app_label="core", model=model).update(app_label="crms")


def backwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    for model in ("product", "odooprofile"):
        ContentType.objects.filter(app_label="crms", model=model).update(app_label="core")


class Migration(migrations.Migration):
    dependencies = [
        ("crms", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
