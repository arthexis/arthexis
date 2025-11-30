from django.core.management.color import no_style
from django.db import migrations


def _copy_badge_counters(apps, schema_editor):
    new_model = apps.get_model("counters", "BadgeCounter")
    try:
        old_model = apps.get_model("nodes", "BadgeCounter")
    except LookupError:
        old_model = None

    if old_model is None:
        return

    for badge in old_model.objects.all():
        new_model.objects.update_or_create(
            pk=badge.pk,
            defaults={
                "name": badge.name,
                "label_template": badge.label_template,
                "priority": badge.priority,
                "separator": badge.separator,
                "primary_source_type": badge.primary_source_type,
                "primary_source": badge.primary_source,
                "secondary_source_type": badge.secondary_source_type,
                "secondary_source": badge.secondary_source,
                "css_class": badge.css_class,
                "is_enabled": badge.is_enabled,
                "content_type_id": badge.content_type_id,
            },
        )

    connection = schema_editor.connection
    for sql in connection.ops.sequence_reset_sql(no_style(), [new_model]):
        schema_editor.execute(sql)


def _copy_dashboard_rules(apps, schema_editor):
    new_model = apps.get_model("counters", "DashboardRule")
    try:
        old_model = apps.get_model("pages", "DashboardRule")
    except LookupError:
        old_model = None

    if old_model is None:
        return

    for rule in old_model.objects.all():
        new_model.objects.update_or_create(
            pk=rule.pk,
            defaults={
                "is_seed_data": rule.is_seed_data,
                "is_user_data": rule.is_user_data,
                "is_deleted": rule.is_deleted,
                "name": rule.name,
                "content_type_id": rule.content_type_id,
                "implementation": rule.implementation,
                "condition": rule.condition,
                "function_name": rule.function_name,
                "success_message": rule.success_message,
                "failure_message": rule.failure_message,
            },
        )

    connection = schema_editor.connection
    for sql in connection.ops.sequence_reset_sql(no_style(), [new_model]):
        schema_editor.execute(sql)


class Migration(migrations.Migration):

    dependencies = [
        ("counters", "0001_initial"),
        ("nodes", "0002_remove_node_constellation_device_and_more"),
        ("pages", "0004_alter_odoochatbridge_profile"),
    ]

    operations = [
        migrations.RunPython(_copy_badge_counters, migrations.RunPython.noop),
        migrations.RunPython(_copy_dashboard_rules, migrations.RunPython.noop),
    ]
