from django.db import migrations


def add_site_operator_group(apps, schema_editor):
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    User = apps.get_model("users", "User")

    group, _ = SecurityGroup.objects.using(schema_editor.connection.alias).get_or_create(
        name="Site Operator"
    )

    admin_user = User.objects.using(schema_editor.connection.alias).filter(
        username="admin"
    ).first()
    if admin_user is not None:
        admin_user.groups.add(group)


class Migration(migrations.Migration):

    dependencies = [
        ("groups", "0002_initial"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_site_operator_group, migrations.RunPython.noop),
    ]
