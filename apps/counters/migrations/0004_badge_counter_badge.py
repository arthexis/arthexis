from django.db import migrations


BADGE_NAME = "Badge Counters"
BADGE_CALLABLE = "apps.counters.badge_values.badge_counter_count"


def create_badge_counter_badge(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    BadgeCounter = apps.get_model("counters", "BadgeCounter")

    content_type = ContentType.objects.filter(app_label="counters", model="badgecounter").first()
    if content_type is None:
        return

    BadgeCounter.objects.get_or_create(
        content_type=content_type,
        name=BADGE_NAME,
        defaults={
            "priority": 0,
            "primary_source_type": "callable",
            "primary_source": BADGE_CALLABLE,
            "secondary_source_type": None,
            "secondary_source": "",
            "label_template": "",
            "separator": "/",
            "css_class": "badge-counter",
            "is_enabled": True,
            "is_seed_data": True,
        },
    )


def remove_badge_counter_badge(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    BadgeCounter = apps.get_model("counters", "BadgeCounter")

    content_type = ContentType.objects.filter(app_label="counters", model="badgecounter").first()
    if content_type is None:
        return

    BadgeCounter.objects.filter(content_type=content_type, name=BADGE_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("counters", "0003_rfid_badge_counter"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(create_badge_counter_badge, remove_badge_counter_badge),
    ]
