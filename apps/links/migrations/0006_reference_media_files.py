from django.db import migrations, models

from apps.media.migrations_utils import copy_to_media

REFERENCE_FILE_BUCKET_SLUG = "links-reference-files"
REFERENCE_FILE_ALLOWED_PATTERNS = "\n".join(
    [
        "*.pdf",
        "*.txt",
        "*.csv",
        "*.md",
        "*.doc",
        "*.docx",
        "*.xls",
        "*.xlsx",
        "*.ppt",
        "*.pptx",
        "*.zip",
        "*.png",
        "*.jpg",
        "*.jpeg",
    ]
)
REFERENCE_QR_BUCKET_SLUG = "links-reference-qr"
REFERENCE_QR_ALLOWED_PATTERNS = "\n".join(["*.png"])


def migrate_reference_files(apps, schema_editor):
    Reference = apps.get_model("links", "Reference")
    MediaBucket = apps.get_model("media", "MediaBucket")
    MediaFile = apps.get_model("media", "MediaFile")

    file_bucket, _ = MediaBucket.objects.update_or_create(
        slug=REFERENCE_FILE_BUCKET_SLUG,
        defaults={
            "name": "Reference Files",
            "allowed_patterns": REFERENCE_FILE_ALLOWED_PATTERNS,
            "max_bytes": 10 * 1024 * 1024,
            "expires_at": None,
        },
    )
    qr_bucket, _ = MediaBucket.objects.update_or_create(
        slug=REFERENCE_QR_BUCKET_SLUG,
        defaults={
            "name": "Reference QR Images",
            "allowed_patterns": REFERENCE_QR_ALLOWED_PATTERNS,
            "max_bytes": 512 * 1024,
            "expires_at": None,
        },
    )

    for reference in Reference.objects.exclude(file=""):
        old_file = getattr(reference, "file", None)
        media_file = copy_to_media(file_bucket, MediaFile, old_file)
        if media_file:
            Reference.objects.filter(pk=reference.pk).update(file_media=media_file)

    for reference in Reference.objects.exclude(image=""):
        old_file = getattr(reference, "image", None)
        media_file = copy_to_media(qr_bucket, MediaFile, old_file)
        if media_file:
            Reference.objects.filter(pk=reference.pk).update(image_media=media_file)


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
        ("links", "0005_update_reference_alt_text_rpi"),
    ]

    operations = [
        migrations.AddField(
            model_name="reference",
            name="file_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="reference_files",
                to="media.mediafile",
                verbose_name="File",
            ),
        ),
        migrations.AddField(
            model_name="reference",
            name="image_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="reference_images",
                to="media.mediafile",
                verbose_name="Image",
            ),
        ),
        migrations.RunPython(migrate_reference_files, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="reference",
            name="file",
        ),
        migrations.RemoveField(
            model_name="reference",
            name="image",
        ),
    ]
