from pathlib import Path

from django.db import migrations, models


TASK_CATEGORY_BUCKET_SLUG = "tasks-category-images"
TASK_CATEGORY_ALLOWED_PATTERNS = "\n".join(["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"])


def _copy_to_media(bucket, media_model, old_file):
    if not old_file:
        return None
    filename = Path(old_file.name).name
    old_file.open("rb")
    try:
        media_file = media_model(
            bucket=bucket,
            original_name=filename,
            content_type=getattr(old_file, "content_type", "") or "",
            size=getattr(old_file, "size", 0) or 0,
        )
        media_file.file.save(filename, old_file, save=False)
        media_file.save()
    finally:
        old_file.close()
    try:
        old_file.delete(save=False)
    except Exception:
        pass
    return media_file


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
        media_file = _copy_to_media(bucket, MediaFile, old_file)
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
