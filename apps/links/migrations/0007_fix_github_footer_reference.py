from __future__ import annotations

from django.db import migrations


OLD_GITHUB_FOOTER_VARIANTS = [
    ("GitHub Repos", "https://github.com/orgs/arthexis/repositories"),
    ("GitHub Repositories", "https://github.com/orgs/arthexis/repositories"),
    ("GitHub Repo", "https://github.com/arthexis/arthexis"),
]

NEW_ALT_TEXT = "Github Repo"
NEW_VALUE = "https://github.com/arthexis/arthexis"


def fix_github_footer_reference(apps, schema_editor) -> None:
    Reference = apps.get_model("links", "Reference")

    candidate_rows = []
    for alt_text, value in OLD_GITHUB_FOOTER_VARIANTS:
        candidate_rows.extend(
            Reference.objects.filter(
                alt_text=alt_text,
                value=value,
                is_seed_data=True,
            ).order_by("pk")
        )

    if not candidate_rows:
        candidate_rows.extend(
            Reference.objects.filter(
                alt_text=NEW_ALT_TEXT,
                value=NEW_VALUE,
                is_seed_data=True,
            ).order_by("pk")
        )

    if not candidate_rows:
        return

    keep = candidate_rows[0]
    duplicate_pks = [row.pk for row in candidate_rows[1:]]
    if duplicate_pks:
        Reference.all_objects.filter(pk__in=duplicate_pks)._raw_delete(
            schema_editor.connection.alias
        )

    if keep.alt_text != NEW_ALT_TEXT or keep.value != NEW_VALUE:
        keep.alt_text = NEW_ALT_TEXT
        keep.value = NEW_VALUE
        keep.save(update_fields=["alt_text", "value"])


def noop_reverse(apps, schema_editor) -> None:
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("links", "0006_referenceattachment"),
    ]

    operations = [
        migrations.RunPython(fix_github_footer_reference, noop_reverse),
    ]
