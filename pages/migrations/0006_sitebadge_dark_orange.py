from django.db import migrations, models
from django.db.models import Q


DARK_ORANGE = "#ff8c00"
OLD_DEFAULT = "#28a745"


def update_router_badge_color(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    SiteBadge = apps.get_model("pages", "SiteBadge")

    router_sites = Site.objects.filter(
        Q(name__iexact="Router")
        | Q(domain__iexact="router")
        | Q(domain__iexact="10.42.0.1")
    )
    if not router_sites:
        return

    SiteBadge.objects.filter(site__in=router_sites, badge_color=OLD_DEFAULT).update(
        badge_color=DARK_ORANGE
    )


def revert_router_badge_color(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    SiteBadge = apps.get_model("pages", "SiteBadge")

    router_sites = Site.objects.filter(
        Q(name__iexact="Router")
        | Q(domain__iexact="router")
        | Q(domain__iexact="10.42.0.1")
    )
    if not router_sites:
        return

    SiteBadge.objects.filter(site__in=router_sites, badge_color=DARK_ORANGE).update(
        badge_color=OLD_DEFAULT
    )


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0005_hide_constellation_rfid"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitebadge",
            name="badge_color",
            field=models.CharField(default="#ff8c00", max_length=7),
        ),
        migrations.RunPython(update_router_badge_color, revert_router_badge_color),
    ]
