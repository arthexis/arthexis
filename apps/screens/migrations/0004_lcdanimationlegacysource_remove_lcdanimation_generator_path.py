from django.db import migrations, models


DEFAULT_ANIMATION_NAME = "Scrolling Trees"
DEFAULT_ANIMATION_SLUG = "scrolling-trees"
DEFAULT_ANIMATION_SOURCE = "scrolling_trees.txt"


def copy_generator_paths_to_legacy_model(apps, schema_editor):
    """Persist legacy generator references before removing the runtime field."""

    LCDAnimation = apps.get_model("screens", "LCDAnimation")
    LCDAnimationLegacySource = apps.get_model("screens", "LCDAnimationLegacySource")

    for animation in LCDAnimation.objects.exclude(generator_path=""):
        LCDAnimationLegacySource.objects.update_or_create(
            animation=animation,
            defaults={
                "generator_path": animation.generator_path,
                "is_seed_data": animation.is_seed_data,
                "is_user_data": animation.is_user_data,
                "is_deleted": animation.is_deleted,
            },
        )


def restore_generator_paths_from_legacy_model(apps, schema_editor):
    """Restore archived generator references when this migration is reversed."""

    LCDAnimation = apps.get_model("screens", "LCDAnimation")
    LCDAnimationLegacySource = apps.get_model("screens", "LCDAnimationLegacySource")

    for legacy_source in LCDAnimationLegacySource.objects.select_related("animation"):
        LCDAnimation.objects.filter(pk=legacy_source.animation_id).update(
            generator_path=legacy_source.generator_path
        )


def seed_default_packaged_animation(apps, schema_editor):
    """Ensure the built-in scrolling trees animation remains available."""

    LCDAnimation = apps.get_model("screens", "LCDAnimation")
    LCDAnimation.objects.update_or_create(
        slug=DEFAULT_ANIMATION_SLUG,
        defaults={
            "name": DEFAULT_ANIMATION_NAME,
            "description": "Bundled scrolling tree animation.",
            "source_path": DEFAULT_ANIMATION_SOURCE,
            "frame_interval_ms": 750,
            "is_active": True,
            "is_seed_data": True,
        },
    )


def unseed_default_packaged_animation(apps, schema_editor):
    """Remove the seeded bundled animation when the migration is reversed."""

    LCDAnimation = apps.get_model("screens", "LCDAnimation")
    LCDAnimation.objects.filter(
        slug=DEFAULT_ANIMATION_SLUG,
        source_path=DEFAULT_ANIMATION_SOURCE,
        is_seed_data=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("screens", "0003_lcdanimation"),
    ]

    operations = [
        migrations.CreateModel(
            name="LCDAnimationLegacySource",
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
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                ("generator_path", models.CharField(max_length=255)),
                (
                    "animation",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="legacy_source",
                        to="screens.lcdanimation",
                    ),
                ),
            ],
            options={
                "verbose_name": "LCD Animation Legacy Source",
                "verbose_name_plural": "LCD Animation Legacy Sources",
                "ordering": ["animation__name"],
                "abstract": False,
            },
        ),
        migrations.RunPython(
            copy_generator_paths_to_legacy_model,
            reverse_code=restore_generator_paths_from_legacy_model,
        ),
        migrations.RunPython(
            seed_default_packaged_animation,
            reverse_code=unseed_default_packaged_animation,
        ),
        migrations.AlterField(
            model_name="lcdanimation",
            name="source_path",
            field=models.CharField(
                blank=True,
                help_text="Packaged animation file name under apps/screens/animations/.",
                max_length=255,
            ),
        ),
        migrations.RemoveField(
            model_name="lcdanimation",
            name="generator_path",
        ),
    ]
