from __future__ import annotations

from django.db import migrations
from django.db.models import Q

OLD_FOOTER_VARIANTS = [
    ("Wizards of the Coast", "https://company.wizards.com/"),
    ("The Wizards", "https://company.wizards.com/"),
]
NEW_ALT_TEXT = "The Workgroup"
NEW_VALUE = "/workgroup/"


def replace_wizards_with_workgroup_footer(apps, schema_editor) -> None:
    """Canonicalize wizard/workgroup footer references into one public workgroup link."""
    Reference = apps.get_model("links", "Reference")
    manager = getattr(Reference, "all_objects", Reference._base_manager)
    db_manager = manager.using(schema_editor.connection.alias)

    filters = Q(alt_text=NEW_ALT_TEXT, value=NEW_VALUE)
    for alt_text, value in OLD_FOOTER_VARIANTS:
        filters |= Q(alt_text=alt_text, value=value)

    candidates = list(db_manager.filter(filters).order_by("pk"))
    if not candidates:
        return

    keep = next(
        (
            row
            for row in candidates
            if row.alt_text == NEW_ALT_TEXT and row.value == NEW_VALUE
        ),
        candidates[0],
    )

    duplicate_pks = [row.pk for row in candidates if row.pk != keep.pk]
    if duplicate_pks:
        db_manager.filter(pk__in=duplicate_pks).delete()

    changed_fields = []
    for field_name, value in (
        ("alt_text", NEW_ALT_TEXT),
        ("value", NEW_VALUE),
        ("method", "link"),
        ("include_in_footer", True),
        ("footer_visibility", "public"),
        ("is_seed_data", True),
    ):
        if getattr(keep, field_name) != value:
            setattr(keep, field_name, value)
            changed_fields.append(field_name)
    if changed_fields:
        keep.save(
            update_fields=changed_fields,
            using=schema_editor.connection.alias,
        )


def noop_reverse(apps, schema_editor) -> None:
    """Leave reverse as a no-op because deleted duplicates cannot be reconstructed."""
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("links", "0007_fix_github_footer_reference"),
    ]

    operations = [
        migrations.RunPython(replace_wizards_with_workgroup_footer, noop_reverse),
    ]
