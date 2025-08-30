from django.db import migrations, models
import django.contrib.auth.models
import django.db.models.deletion
from django.conf import settings
from django.contrib.auth.hashers import make_password

# NOTE: Prefer rewriting this latest migration over creating new ones.
# Earlier migrations must be preserved to maintain compatibility.


def create_securitygroups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    SecurityGroup = apps.get_model('core', 'SecurityGroup')
    for group in Group.objects.all():
        SecurityGroup.objects.get_or_create(group_ptr=group)


def create_packaging_defaults(apps, schema_editor):
    """Ensure default package and packager profile exist."""

    app_label, model_name = settings.AUTH_USER_MODEL.split('.')
    User = apps.get_model(app_label, model_name)
    PackagerProfile = apps.get_model('core', 'PackagerProfile')
    Package = apps.get_model('core', 'Package')

    User.objects.get_or_create(
        username="admin",
        defaults={
            "email": "",
            "is_staff": True,
            "is_superuser": True,
            "password": make_password("admin"),
        },
    )

    user, _ = User.objects.get_or_create(
        username="arthexis",
        defaults={
            "email": "tecnologia@gelectriic.com",
            "is_staff": True,
            "is_superuser": True,
        },
    )
    profile, _ = PackagerProfile.objects.get_or_create(
        user=user,
        defaults={
            "username": "arthexis",
            "pypi_url": "https://pypi.org/user/arthexis/",
        },
    )
    Package.objects.get_or_create(
        name="arthexis",
        defaults={
            "description": "Django-based MESH system",
            "author": "Rafael J. Guillén-Osorio",
            "email": "tecnologia@gelectriic.com",
            "python_requires": ">=3.10",
            "license": "MIT",
            "repository_url": "https://github.com/arthexis/arthexis",
            "homepage_url": "https://arthexis.com",
            "release_manager": profile,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_reference_transaction_uuid'),
    ]

    operations = [
        migrations.CreateModel(
            name='SecurityGroup',
            fields=[
                (
                    'group_ptr',
                    models.OneToOneField(
                        auto_created=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to='auth.group',
                    ),
                ),
                (
                    'parent',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='children',
                        to='core.securitygroup',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Security Group',
                'verbose_name_plural': 'Security Groups',
            },
            bases=('auth.group',),
            managers=[('objects', django.contrib.auth.models.GroupManager()),],
        ),
        migrations.RunPython(create_securitygroups, migrations.RunPython.noop),
        migrations.RunPython(create_packaging_defaults, migrations.RunPython.noop),
    ]

