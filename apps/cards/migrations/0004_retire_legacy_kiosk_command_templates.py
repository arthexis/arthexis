from urllib.parse import urlsplit

from django.db import migrations


def _is_legacy_kiosk_target(value):
    target = (value or "").strip()
    if not target:
        return False
    path = urlsplit(target).path
    normalized = path.rstrip("/")
    return normalized == "/kiosk" or normalized.startswith("/kiosk/")


def retire_legacy_kiosk_command_templates(apps, schema_editor):
    template_model = apps.get_model("cards", "RFIDCommandTemplate")

    template_model._base_manager.filter(view_kind="kiosk").update(view_kind="general")
    for template in template_model._base_manager.exclude(qr_target_path="").iterator():
        if _is_legacy_kiosk_target(template.qr_target_path):
            template_model._base_manager.filter(pk=template.pk).update(qr_target_path="")


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0003_initial"),
    ]

    operations = [
        migrations.RunPython(
            retire_legacy_kiosk_command_templates,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
