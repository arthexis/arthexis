from pathlib import Path

from django.db import migrations, models


MODULE_FAVICON_BUCKET_SLUG = "modules-favicons"
MODULE_FAVICON_ALLOWED_PATTERNS = "\n".join(["*.png", "*.ico", "*.svg", "*.jpg", "*.jpeg"])


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


def migrate_module_favicons(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    MediaBucket = apps.get_model("media", "MediaBucket")
    MediaFile = apps.get_model("media", "MediaFile")

    bucket, _ = MediaBucket.objects.update_or_create(
        slug=MODULE_FAVICON_BUCKET_SLUG,
        defaults={
            "name": "Module Favicons",
            "allowed_patterns": MODULE_FAVICON_ALLOWED_PATTERNS,
            "max_bytes": 512 * 1024,
            "expires_at": None,
        },
    )

    for module in Module.objects.exclude(favicon=""):
        old_file = getattr(module, "favicon", None)
        media_file = _copy_to_media(bucket, MediaFile, old_file)
        if media_file:
            Module.objects.filter(pk=module.pk).update(favicon_media=media_file)


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
        ("modules", "0003_normalize_module_paths"),
    ]

    operations = [
        migrations.AddField(
            model_name="module",
            name="favicon_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="module_favicons",
                to="media.mediafile",
                verbose_name="Favicon",
            ),
        ),
        migrations.RunPython(migrate_module_favicons, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="module",
            name="favicon",
        ),
    ]
