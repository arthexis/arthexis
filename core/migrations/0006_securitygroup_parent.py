from django.db import migrations, models
import django.contrib.auth.models
import django.db.models.deletion


def create_securitygroups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    SecurityGroup = apps.get_model('core', 'SecurityGroup')
    for group in Group.objects.all():
        SecurityGroup.objects.get_or_create(group_ptr=group)


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
    ]

