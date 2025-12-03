from django.db import migrations
from django.db.models import Count, F


def _delete_orphan_application_landings(apps, schema_editor):
    Module = apps.get_model("pages", "Module")

    modules_with_fallback_landing = Module.objects.annotate(
        landing_count=Count("landings")
    ).filter(
        landing_count=1,
        landings__path=F("path"),
        application__isnull=False,
        is_seed_data=True,
    )

    if modules_with_fallback_landing:
        Module.objects.filter(
            pk__in=modules_with_fallback_landing.values_list("pk", flat=True)
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0004_landing_validated_url_at_landing_validation_status_and_more"),
    ]

    operations = [
        migrations.RunPython(
            _delete_orphan_application_landings, migrations.RunPython.noop
        )
    ]
