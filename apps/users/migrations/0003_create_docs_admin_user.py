from django.contrib.auth.hashers import make_password
from django.db import migrations


def create_docs_admin_user(apps, schema_editor):
    User = apps.get_model("users", "User")
    if User.objects.filter(username="docs").exists():
        return
    User.objects.create(
        username="docs",
        password=make_password("docs"),
        is_staff=True,
        is_superuser=True,
        is_active=True,
    )


def remove_docs_admin_user(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(username="docs").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0002_totp"),
    ]

    operations = [
        migrations.RunPython(create_docs_admin_user, remove_docs_admin_user),
    ]
