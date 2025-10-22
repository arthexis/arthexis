from django.db import migrations


LANDING_PATH_UPDATES = {
    "/ocpp/": "/ocpp/cpms/dashboard/",
    "/ocpp/simulator/": "/ocpp/evcs/simulator/",
    "/ocpp/rfid/": "/ocpp/rfid/validator/",
}


def forwards(apps, schema_editor):
    Landing = apps.get_model("pages", "Landing")
    RoleLanding = apps.get_model("pages", "RoleLanding")
    LandingLead = apps.get_model("pages", "LandingLead")
    SiteBadge = apps.get_model("pages", "SiteBadge")

    for old_path, new_path in LANDING_PATH_UPDATES.items():
        for landing in Landing.objects.filter(path=old_path):
            duplicate = (
                Landing.objects.filter(module=landing.module, path=new_path)
                .exclude(pk=landing.pk)
                .first()
            )

            if duplicate:
                RoleLanding.objects.filter(landing=landing).update(landing=duplicate)
                LandingLead.objects.filter(landing=landing).update(landing=duplicate)
                SiteBadge.objects.filter(landing_override=landing).update(
                    landing_override=duplicate
                )

                if not landing.is_deleted:
                    landing.is_deleted = True
                    landing.save(update_fields=["is_deleted"])

                continue

            landing.path = new_path
            landing.save(update_fields=["path"])


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0026_update_awg_landing_label"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
