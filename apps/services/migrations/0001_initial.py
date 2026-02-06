from __future__ import annotations

from django.db import migrations, models


def seed_lifecycle_services(apps, schema_editor):
    LifecycleService = apps.get_model("services", "LifecycleService")
    defaults = [
        {
            "slug": "suite",
            "display": "Suite service",
            "unit_template": "{service}.service",
            "pid_file": "django.pid",
            "docs_path": "services/suite-service.md",
            "activation": "always",
            "feature_slug": "",
            "lock_names": [],
            "sort_order": 10,
        },
        {
            "slug": "celery-worker",
            "display": "Celery worker",
            "unit_template": "celery-{service}.service",
            "pid_file": "celery_worker.pid",
            "docs_path": "services/celery-worker.md",
            "activation": "lockfile",
            "feature_slug": "",
            "lock_names": ["celery.lck"],
            "sort_order": 20,
        },
        {
            "slug": "celery-beat",
            "display": "Celery beat",
            "unit_template": "celery-beat-{service}.service",
            "pid_file": "celery_beat.pid",
            "docs_path": "services/celery-beat.md",
            "activation": "lockfile",
            "feature_slug": "",
            "lock_names": ["celery.lck"],
            "sort_order": 30,
        },
        {
            "slug": "lcd-screen",
            "display": "LCD screen",
            "unit_template": "lcd-{service}.service",
            "pid_file": "lcd.pid",
            "docs_path": "services/lcd-screen.md",
            "activation": "lockfile",
            "feature_slug": "",
            "lock_names": [
                "lcd-high",
                "lcd-low",
                "clock",
                "uptime",
                "stats",
                "lcd-channels.lck",
                "lcd_screen_enabled.lck",
                "lcd_screen.lck",
            ],
            "sort_order": 40,
        },
        {
            "slug": "rfid-service",
            "display": "RFID scanner service",
            "unit_template": "rfid-{service}.service",
            "pid_file": "",
            "docs_path": "services/rfid-scanner-service.md",
            "activation": "lockfile",
            "feature_slug": "",
            "lock_names": ["rfid-service.lck"],
            "sort_order": 50,
        },
        {
            "slug": "camera-service",
            "display": "Camera capture service",
            "unit_template": "camera-{service}.service",
            "pid_file": "",
            "docs_path": "",
            "activation": "lockfile",
            "feature_slug": "",
            "lock_names": ["camera-service.lck"],
            "sort_order": 60,
        },
    ]

    for entry in defaults:
        LifecycleService.objects.update_or_create(slug=entry["slug"], defaults=entry)


def unseed_lifecycle_services(apps, schema_editor):
    LifecycleService = apps.get_model("services", "LifecycleService")
    slugs = [
        "suite",
        "celery-worker",
        "celery-beat",
        "lcd-screen",
        "rfid-service",
        "camera-service",
    ]
    LifecycleService.objects.filter(slug__in=slugs).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="LifecycleService",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("slug", models.SlugField(max_length=64, unique=True)),
                ("display", models.CharField(max_length=80)),
                (
                    "unit_template",
                    models.CharField(
                        help_text='Systemd unit template, for example "celery-{service}.service".',
                        max_length=120,
                    ),
                ),
                ("pid_file", models.CharField(blank=True, max_length=120)),
                ("docs_path", models.CharField(blank=True, max_length=160)),
                (
                    "activation",
                    models.CharField(
                        choices=[
                            ("always", "Always enabled"),
                            ("feature", "Node feature"),
                            ("lockfile", "Lock file"),
                            ("manual", "Manual"),
                        ],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("feature_slug", models.SlugField(blank=True, max_length=64)),
                ("lock_names", models.JSONField(blank=True, default=list)),
                ("sort_order", models.PositiveIntegerField(default=0)),
            ],
            options={
                "verbose_name": "Lifecycle Service",
                "verbose_name_plural": "Lifecycle Services",
                "ordering": ["sort_order", "display"],
            },
        ),
        migrations.RunPython(seed_lifecycle_services, unseed_lifecycle_services),
    ]
