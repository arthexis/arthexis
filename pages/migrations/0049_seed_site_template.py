from django.db import migrations


def seed_site_templates(apps, schema_editor):
    SiteTemplate = apps.get_model("pages", "SiteTemplate")
    Site = apps.get_model("sites", "Site")

    template, _created = SiteTemplate.objects.get_or_create(
        name="Constellation",
        defaults={
            "is_seed_data": True,
            "primary_color": "#0d6efd",
            "primary_color_emphasis": "#0b5ed7",
            "accent_color": "#facc15",
            "accent_color_emphasis": "#fb923c",
            "support_color": "#15803d",
            "support_color_emphasis": "#34d399",
            "support_text_color": "#f0fdf4",
        },
    )

    if not template.is_seed_data:
        template.is_seed_data = True
        template.save(update_fields=["is_seed_data"])

    for site in Site.objects.all():
        if getattr(site, "template_id", None) is None:
            site.template = template
            site.save(update_fields=["template"])


def noop(_apps, _schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0048_site_template_on_site"),
    ]

    operations = [migrations.RunPython(seed_site_templates, noop)]
