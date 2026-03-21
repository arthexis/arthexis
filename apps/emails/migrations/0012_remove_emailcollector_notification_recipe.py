from django.db import migrations


def reject_collectors_using_notification_recipe(apps, schema_editor):
    """Abort the migration if collectors still reference unsupported recipes."""
    EmailCollector = apps.get_model("emails", "EmailCollector")
    configured_collectors = list(
        EmailCollector.objects.filter(notification_recipe__isnull=False)
        .order_by("pk")
        .values_list("pk", "name")
    )
    if not configured_collectors:
        return

    configured_labels = ", ".join(
        f"#{collector_id} ({collector_name or 'unnamed collector'})"
        for collector_id, collector_name in configured_collectors
    )
    raise RuntimeError(
        "Cannot remove EmailCollector.notification_recipe while collectors still "
        f"reference recipes: {configured_labels}. Clear those recipe references "
        "before applying this migration."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("emails", "0011_merge_20260310_1506"),
    ]

    operations = [
        migrations.RunPython(
            reject_collectors_using_notification_recipe,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="emailcollector",
            name="notification_recipe",
        ),
    ]
