from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("modules", "0004_module_favicon_media"),
        ("nodes", "0019_alter_netmessage_lcd_channel_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="module",
            name="features",
            field=models.ManyToManyField(
                blank=True,
                help_text="Require these node features to be enabled for this module to appear.",
                related_name="modules",
                to="nodes.nodefeature",
            ),
        ),
    ]
