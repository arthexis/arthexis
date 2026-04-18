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
        "GitHub Repos",
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
        old_seed_rows = list(
            Reference.objects.filter(
                alt_text=old_alt,
                value=value,
                is_seed_data=True,
            ).order_by("pk")
        )
        target_rows = list(
            Reference.objects.filter(
                alt_text=new_alt,
                value=value,
            ).order_by("pk")
        )
        if not old_seed_rows and not target_rows:
            continue

        keep = next((row for row in target_rows if not row.is_seed_data), None)
        if keep is None and old_seed_rows:
            keep = old_seed_rows[0]
        if keep is None:
            keep = target_rows[0]

        duplicate_pks = [row.pk for row in old_seed_rows + target_rows if row.pk != keep.pk]
        if duplicate_pks:
            Reference.all_objects.filter(pk__in=duplicate_pks)._raw_delete(
                schema_editor.connection.alias
            )

        if keep.alt_text != new_alt:
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
