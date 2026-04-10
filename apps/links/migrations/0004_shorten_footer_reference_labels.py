from __future__ import annotations

from django.db import migrations


FOOTER_REFERENCE_LABEL_RENAMES = [
    (
        "The Python Foundation",
        "https://www.python.org/",
        "The Foundation",
    ),
    (
        "Open Charge Point Protocol",
        "https://openchargealliance.org/protocols/open-charge-point-protocol/",
        "Open CP Protocol",
    ),
    (
        "GitHub Repositories",
        "https://github.com/orgs/arthexis/repositories",
        "Github repos",
    ),
    (
        "ARG 1.0 (The Arthexis Reciprocity License)",
        "https://github.com/arthexis/arthexis/blob/main/LICENSE",
        "ARG License 1.0",
    ),
    (
        "Wizards of the Coast",
        "https://company.wizards.com/",
        "The Wizards",
    ),
]


def shorten_footer_seed_reference_labels(apps, schema_editor) -> None:
    Reference = apps.get_model("links", "Reference")

    for old_alt, value, new_alt in FOOTER_REFERENCE_LABEL_RENAMES:
        old_rows = list(
            Reference.objects.filter(
                alt_text=old_alt,
                value=value,
                is_seed_data=True,
            ).order_by("pk")
        )
        new_rows = list(
            Reference.objects.filter(
                alt_text=new_alt,
                value=value,
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
        keep.save(update_fields=["alt_text"])


def noop_reverse(apps, schema_editor) -> None:
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("links", "0003_update_footer_reference_seed_keys"),
    ]

    operations = [
        migrations.RunPython(shorten_footer_seed_reference_labels, noop_reverse),
    ]
