from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('release', '0008_packageconfig_is_deleted_packageconfig_is_seed_data_and_more'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='PackageConfig',
            new_name='PackageRelease',
        ),
        migrations.AlterModelOptions(
            name='packagerelease',
            options={'verbose_name': 'Package Release', 'verbose_name_plural': 'Package Releases'},
        ),
    ]
