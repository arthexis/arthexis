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
    """Ensure default package and release manager exist."""

    app_label, model_name = settings.AUTH_USER_MODEL.split('.')
    User = apps.get_model(app_label, model_name)
    ReleaseManager = apps.get_model('core', 'ReleaseManager')
    Package = apps.get_model('core', 'Package')

    user, _ = User.objects.get_or_create(
        username="arthexis",
        defaults={
            "email": "tecnologia@gelectriic.com",
            "is_staff": True,
            "is_superuser": True,
        },
    )
    manager, _ = ReleaseManager.objects.get_or_create(
        user=user,
        defaults={
            "pypi_username": "arthexis",
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
            "release_manager": manager,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_reference_transaction_uuid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='releasemanager',
            name='pypi_token',
            field=models.CharField("PyPI token", blank=True, max_length=200),
        ),
        migrations.AlterField(
            model_name='releasemanager',
            name='pypi_username',
            field=models.CharField("PyPI username", blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name='releasemanager',
            name='pypi_password',
            field=models.CharField("PyPI password", blank=True, max_length=200),
        ),
        migrations.AlterField(
            model_name='releasemanager',
            name='pypi_url',
            field=models.URLField(blank=True, verbose_name='PyPI URL'),
        ),
        migrations.AddField(
            model_name='reference',
            name='footer_visibility',
            field=models.CharField(
                choices=[('public', 'Public'), ('private', 'Private'), ('staff', 'Staff')],
                default='public',
                max_length=7,
                verbose_name='Footer visibility',
            ),
        ),
        migrations.RenameModel(
            old_name='Vehicle',
            new_name='ElectricVehicle',
        ),
        migrations.AlterModelOptions(
            name='electricvehicle',
            options={
                'verbose_name': 'Electric Vehicle',
                'verbose_name_plural': 'Electric Vehicles',
            },
        ),
        migrations.CreateModel(
            name='EmailCollector',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_seed_data', models.BooleanField(default=False, editable=False)),
                ('is_deleted', models.BooleanField(default=False, editable=False)),
                ('subject', models.CharField(blank=True, max_length=255)),
                ('sender', models.CharField(blank=True, max_length=255)),
                ('body', models.CharField(blank=True, max_length=255)),
                (
                    'fragment',
                    models.CharField(
                        blank=True,
                        max_length=255,
                        help_text='Pattern with [sigils] to extract values from the body.',
                    ),
                ),
                ('inbox', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='collectors', to='core.emailinbox')),
            ],
            options={
                'verbose_name': 'Email Collector',
                'verbose_name_plural': 'Email Collectors',
            },
        ),
        migrations.CreateModel(
            name='EmailArtifact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_seed_data', models.BooleanField(default=False, editable=False)),
                ('is_deleted', models.BooleanField(default=False, editable=False)),
                ('subject', models.CharField(max_length=255)),
                ('sender', models.CharField(max_length=255)),
                ('body', models.TextField(blank=True)),
                ('sigils', models.JSONField(default=dict)),
                ('fingerprint', models.CharField(max_length=32)),
                ('collector', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='artifacts', to='core.emailcollector')),
            ],
            options={
                'verbose_name': 'Email Artifact',
                'verbose_name_plural': 'Email Artifacts',
                'unique_together': {('collector', 'fingerprint')},
            },
        ),
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

