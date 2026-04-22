from __future__ import annotations

from django.db import migrations
from django.db.models import Q


OLD_GITHUB_FOOTER_VARIANTS = [
    ("GitHub Repos", "https://github.com/orgs/arthexis/repositories"),
    ("GitHub Repositories", "https://github.com/orgs/arthexis/repositories"),
    ("GitHub Repo", "https://github.com/arthexis/arthexis"),
]

NEW_ALT_TEXT = "Github Repo"
NEW_VALUE = "https://github.com/arthexis/arthexis"


def fix_github_footer_reference(apps, schema_editor) -> None:
    Reference = apps.get_model("links", "Reference")
    manager = getattr(Reference, "all_objects", Reference._base_manager)

    variants = [*OLD_GITHUB_FOOTER_VARIANTS, (NEW_ALT_TEXT, NEW_VALUE)]

    filters = Q()
    for alt_text, value in variants:
        filters |= Q(alt_text=alt_text, value=value)

    candidate_rows = list(manager.filter(filters).order_by("pk"))
    if not candidate_rows:
        return

    keep = next(
        (
            row
            for row in candidate_rows
            if row.alt_text == NEW_ALT_TEXT and row.value == NEW_VALUE
        ),
        candidate_rows[0],
    )

    duplicate_pks = [row.pk for row in candidate_rows if row.pk != keep.pk]
    if duplicate_pks:
        manager.using(schema_editor.connection.alias).filter(pk__in=duplicate_pks).delete()

    if keep.alt_text != NEW_ALT_TEXT or keep.value != NEW_VALUE:
        keep.alt_text = NEW_ALT_TEXT
        keep.value = NEW_VALUE
        keep.save(
            update_fields=["alt_text", "value"],
            using=schema_editor.connection.alias,
        )


def noop_reverse(apps, schema_editor) -> None:
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("links", "0006_referenceattachment"),
    ]

    operations = [
        migrations.RunPython(fix_github_footer_reference, noop_reverse),
    ]
