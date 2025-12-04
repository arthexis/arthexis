from django.apps import apps as django_apps
from django.db import migrations, models


def add_missing_descriptions(apps, schema_editor):
    Application = apps.get_model("app", "Application")
    defaults = getattr(
        django_apps.get_app_config("pages"),
        "module",
        None,
    )
    default_descriptions = getattr(defaults, "DEFAULT_APPLICATION_DESCRIPTIONS", {})

    installed_labels = {
        config.label for config in django_apps.get_app_configs()
    }

    for app in Application.objects.filter(
        models.Q(description="") | models.Q(description__isnull=True)
    ):
        description = default_descriptions.get(app.name)
        if not description:
            try:
                config = django_apps.get_app_config(app.name)
            except LookupError:
                config = next(
                    (c for c in django_apps.get_app_configs() if c.name == app.name),
                    None,
                )
            if config:
                description = getattr(config, "verbose_name", None) or config.label
            elif app.name in installed_labels:
                description = app.name
            else:
                description = app.display_name

        Application.objects.filter(pk=app.pk).update(description=description)


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_missing_descriptions, migrations.RunPython.noop),
    ]
