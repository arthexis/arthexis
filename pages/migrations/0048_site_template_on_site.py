from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0047_sitetemplate"),
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="site",
            name="template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="sites",
                to="pages.sitetemplate",
                verbose_name="Template",
            ),
        ),
    ]
