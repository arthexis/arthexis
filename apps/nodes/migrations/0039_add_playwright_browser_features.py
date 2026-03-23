from django.db import migrations


FEATURES = [
    ("playwright-browser-chromium", "Playwright Chromium Browser"),
    ("playwright-browser-firefox", "Playwright Firefox Browser"),
    ("playwright-browser-webkit", "Playwright WebKit Browser"),
]


def add_features(apps, schema_editor):
    del schema_editor
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    for slug, display in FEATURES:
        NodeFeature.objects.update_or_create(slug=slug, defaults={"display": display})


def remove_features(apps, schema_editor):
    del schema_editor
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug__in=[slug for slug, _ in FEATURES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0038_merge_20260301_1900"),
    ]

    operations = [
        migrations.RunPython(add_features, remove_features),
    ]
