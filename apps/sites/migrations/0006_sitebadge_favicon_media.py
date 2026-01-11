from pathlib import Path

from django.db import migrations, models


SITE_BADGE_FAVICON_BUCKET_SLUG = "sites-badge-favicons"
SITE_BADGE_FAVICON_ALLOWED_PATTERNS = "\n".join(["*.png", "*.ico", "*.svg", "*.jpg", "*.jpeg"])


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


def migrate_sitebadge_favicons(apps, schema_editor):
    SiteBadge = apps.get_model("pages", "SiteBadge")
    MediaBucket = apps.get_model("media", "MediaBucket")
    MediaFile = apps.get_model("media", "MediaFile")

    bucket, _ = MediaBucket.objects.update_or_create(
        slug=SITE_BADGE_FAVICON_BUCKET_SLUG,
        defaults={
            "name": "Site Badge Favicons",
            "allowed_patterns": SITE_BADGE_FAVICON_ALLOWED_PATTERNS,
            "max_bytes": 512 * 1024,
            "expires_at": None,
        },
    )

    for badge in SiteBadge.objects.exclude(favicon=""):
        old_file = getattr(badge, "favicon", None)
        media_file = _copy_to_media(bucket, MediaFile, old_file)
        if media_file:
            SiteBadge.objects.filter(pk=badge.pk).update(favicon_media=media_file)


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
        ("pages", "0005_viewhistory_exception_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitebadge",
            name="favicon_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="site_badge_favicons",
                to="media.mediafile",
                verbose_name="Favicon",
            ),
        ),
        migrations.RunPython(migrate_sitebadge_favicons, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="sitebadge",
            name="favicon",
        ),
    ]
