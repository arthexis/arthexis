from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0003_siteproxy"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="App",
            new_name="Application",
        ),
        migrations.AlterField(
            model_name="application",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="applications",
                to="sites.site",
            ),
        ),
    ]

