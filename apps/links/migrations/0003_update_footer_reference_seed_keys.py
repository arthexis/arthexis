from __future__ import annotations

from django.db import migrations


FOOTER_REFERENCE_KEY_RENAMES = [
    (
        "Ubuntu 22.04 LTS",
        "https://releases.ubuntu.com/22.04/",
        "Ubuntu 24.04 LTS",
        "https://releases.ubuntu.com/24.04/",
    ),
    (
        "Arthexis on PyPI",
        "https://pypi.org/project/arthexis/",
        "Python Package Index",
        "https://pypi.org/project/arthexis/",
    ),
    (
        "Python",
        "https://www.python.org/",
        "The Python Foundation",
        "https://www.python.org/",
    ),
    (
        "OCPP",
        "https://openchargealliance.org/protocols/open-charge-point-protocol/",
        "Open Charge Point Protocol",
        "https://openchargealliance.org/protocols/open-charge-point-protocol/",
    ),
    (
        "GitHub Repo",
        "https://github.com/arthexis/arthexis",
        "GitHub Repositories",
        "https://github.com/orgs/arthexis/repositories",
    ),
    (
        "GNU GPLv3",
        "https://www.gnu.org/licenses/gpl-3.0.en.html",
        "ARG 1.0 (The Arthexis Reciprocity License)",
        "https://github.com/arthexis/arthexis/blob/main/LICENSE",
    ),
    (
        "Selenium",
        "https://www.selenium.dev/",
        "Playwright",
        "https://playwright.dev/",
    ),
    (
        "RPi 4 Model B",
        "https://www.raspberrypi.com/products/raspberry-pi-4-model-b/",
        "Raspberry Pi 4B 2GB",
        "https://www.raspberrypi.com/products/raspberry-pi-4-model-b/",
    ),
    (
        "RPi Pinout",
        "https://pinout.xyz/",
        "GPIO Pinout",
        "https://pinout.xyz/",
    ),
]


def update_footer_seed_references(apps, schema_editor) -> None:
    Reference = apps.get_model("links", "Reference")

    for old_alt, old_value, new_alt, new_value in FOOTER_REFERENCE_KEY_RENAMES:
        old_rows = list(
            Reference.objects.filter(
                alt_text=old_alt,
                value=old_value,
                is_seed_data=True,
            ).order_by("pk")
        )
        new_rows = list(
            Reference.objects.filter(
                alt_text=new_alt,
                value=new_value,
                is_seed_data=True,
            ).order_by("pk")
        )
        if not old_rows and not new_rows:
            continue

        keep = old_rows[0] if old_rows else new_rows[0]

        duplicate_pks = [row.pk for row in old_rows[1:] + new_rows]
        if keep.pk in duplicate_pks:
            duplicate_pks.remove(keep.pk)
        if duplicate_pks:
            Reference.all_objects.filter(pk__in=duplicate_pks)._raw_delete(
                schema_editor.connection.alias
            )

        keep.alt_text = new_alt
        keep.value = new_value
        keep.save(update_fields=["alt_text", "value"])


def noop_reverse(apps, schema_editor) -> None:
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("links", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(update_footer_seed_references, noop_reverse),
    ]
