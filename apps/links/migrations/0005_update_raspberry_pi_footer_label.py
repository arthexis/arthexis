from __future__ import annotations

from django.db import migrations


OLD_ALT_TEXT = "Raspberry Pi 4B 2GB"
NEW_ALT_TEXT = "Raspberry Pi 4B 3GB"
RPI4B_VALUE = "https://www.raspberrypi.com/products/raspberry-pi-4-model-b/"


def update_raspberry_pi_footer_label(apps, schema_editor) -> None:
    Reference = apps.get_model("links", "Reference")

    manager = getattr(Reference, "all_objects", Reference._base_manager)

    old_seed_rows = list(
        manager.filter(
            alt_text=OLD_ALT_TEXT,
            value=RPI4B_VALUE,
            is_seed_data=True,
        ).order_by("pk")
    )
    target_rows = list(
        manager.filter(
            alt_text=NEW_ALT_TEXT,
            value=RPI4B_VALUE,
        ).order_by("pk")
    )
    if not old_seed_rows and not target_rows:
        return

    keep = next((row for row in target_rows if not row.is_seed_data), None)
    if keep is None and old_seed_rows:
        keep = old_seed_rows[0]
    if keep is None:
        keep = target_rows[0]

    duplicate_pks = [row.pk for row in old_seed_rows + target_rows if row.pk != keep.pk]
    if duplicate_pks:
        manager.filter(pk__in=duplicate_pks)._raw_delete(
            schema_editor.connection.alias
        )

    if keep.alt_text != NEW_ALT_TEXT:
        keep.alt_text = NEW_ALT_TEXT
        keep.save(update_fields=["alt_text"])


def noop_reverse(apps, schema_editor) -> None:
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("links", "0004_shorten_footer_reference_labels"),
    ]

    operations = [
        migrations.RunPython(update_raspberry_pi_footer_label, noop_reverse),
    ]
