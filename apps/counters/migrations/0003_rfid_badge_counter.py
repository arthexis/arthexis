from django.db import migrations


BADGE_NAME = "RFIDs"
BADGE_CALLABLE = "apps.counters.badge_values.rfid_release_stats"


def create_rfid_badge(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    BadgeCounter = apps.get_model("counters", "BadgeCounter")

    content_type = ContentType.objects.filter(app_label="cards", model="rfid").first()
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


def remove_rfid_badge(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    BadgeCounter = apps.get_model("counters", "BadgeCounter")

    content_type = ContentType.objects.filter(app_label="cards", model="rfid").first()
    if content_type is None:
        return

    BadgeCounter.objects.filter(content_type=content_type, name=BADGE_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("counters", "0002_node_badge_counter"),
        ("cards", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(create_rfid_badge, remove_rfid_badge),
    ]
