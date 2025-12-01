from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):
    dependencies = [
        ("sites", "0002_alter_domain_unique"),
        ("pages", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="site",
            name="managed",
            field=models.BooleanField(
                default=False,
                db_default=False,
                verbose_name=_("Managed by local NGINX"),
                help_text=_("Include this site when staging the local NGINX configuration."),
            ),
        ),
        migrations.AddField(
            model_name="site",
            name="require_https",
            field=models.BooleanField(
                default=False,
                db_default=False,
                verbose_name=_("Require HTTPS"),
                help_text=_(
                    "Redirect HTTP traffic to HTTPS when the staged NGINX configuration is applied."
                ),
            ),
        ),
        migrations.AddField(
            model_name="site",
            name="template",
            field=models.ForeignKey(
                on_delete=models.SET_NULL,
                related_name="sites",
                null=True,
                blank=True,
                to="pages.sitetemplate",
                verbose_name=_("Template"),
            ),
        ),
    ]
