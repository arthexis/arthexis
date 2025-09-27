from django.db import migrations


def ensure_system_user_staff(apps, schema_editor):
    User = apps.get_model("core", "User")
    manager = getattr(User, "all_objects", User._default_manager)
    system_username = getattr(User, "SYSTEM_USERNAME", "arthexis")
    updates = {
        "is_staff": True,
        "is_superuser": True,
    }
    if hasattr(User, "is_active"):
        updates["is_active"] = True
    manager.filter(username=system_username).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0050_emailcollector_name_and_more"),
    ]

    operations = [
        migrations.RunPython(ensure_system_user_staff, migrations.RunPython.noop),
    ]

