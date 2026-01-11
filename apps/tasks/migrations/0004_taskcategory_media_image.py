from django.db import migrations, models

from apps.media.migrations_utils import copy_to_media

TASK_CATEGORY_BUCKET_SLUG = "tasks-category-images"
TASK_CATEGORY_ALLOWED_PATTERNS = "\n".join(["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"])


def migrate_taskcategory_images(apps, schema_editor):
    TaskCategory = apps.get_model("tasks", "TaskCategory")
    MediaBucket = apps.get_model("media", "MediaBucket")
    MediaFile = apps.get_model("media", "MediaFile")

    bucket, _ = MediaBucket.objects.update_or_create(
        slug=TASK_CATEGORY_BUCKET_SLUG,
        defaults={
            "name": "Task Category Images",
            "allowed_patterns": TASK_CATEGORY_ALLOWED_PATTERNS,
            "max_bytes": 2 * 1024 * 1024,
            "expires_at": None,
        },
    )

    for category in TaskCategory.objects.exclude(image=""):
        old_file = getattr(category, "image", None)
        media_file = copy_to_media(bucket, MediaFile, old_file)
        if media_file:
            TaskCategory.objects.filter(pk=category.pk).update(image_media=media_file)


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
        ("tasks", "0003_manualskill_manualtaskrequest_manualtaskreport_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskcategory",
            name="image_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="task_category_images",
                to="media.mediafile",
                verbose_name="Image",
            ),
        ),
        migrations.RunPython(migrate_taskcategory_images, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="taskcategory",
            name="image",
        ),
    ]
