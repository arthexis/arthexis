from django.db import migrations, models


def add_site_template_field(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    SiteTemplate = apps.get_model("pages", "SiteTemplate")
    field = models.ForeignKey(
        SiteTemplate,
        on_delete=models.SET_NULL,
        related_name="sites",
        null=True,
        blank=True,
        verbose_name="Template",
    )
    field.set_attributes_from_name("template")
    schema_editor.add_field(Site, field)


def remove_site_template_field(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    try:
        field = Site._meta.get_field("template")
    except Exception:  # pragma: no cover - defensive downgrade
        return
    schema_editor.remove_field(Site, field)


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0047_sitetemplate"),
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        migrations.RunPython(add_site_template_field, remove_site_template_field),
    ]
