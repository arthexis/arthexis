from django.db import migrations, models

from apps.media.migrations_utils import copy_to_media

CARD_FACE_BUCKET_SLUG = "cards-cardface-backgrounds"
CARD_FACE_ALLOWED_PATTERNS = "\n".join(["*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff"])


def migrate_cardface_backgrounds(apps, schema_editor):
    CardFace = apps.get_model("cards", "CardFace")
    MediaBucket = apps.get_model("media", "MediaBucket")
    MediaFile = apps.get_model("media", "MediaFile")

    bucket, _ = MediaBucket.objects.update_or_create(
        slug=CARD_FACE_BUCKET_SLUG,
        defaults={
            "name": "Card Face Backgrounds",
            "allowed_patterns": CARD_FACE_ALLOWED_PATTERNS,
            "max_bytes": 3 * 1024 * 1024,
            "expires_at": None,
        },
    )

    for face in CardFace.objects.exclude(background=""):
        old_file = getattr(face, "background", None)
        media_file = copy_to_media(bucket, MediaFile, old_file)
        if media_file:
            CardFace.objects.filter(pk=face.pk).update(background_media=media_file)


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
        ("cards", "0003_cardface"),
    ]

    operations = [
        migrations.AddField(
            model_name="cardface",
            name="background_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="cardface_backgrounds",
                to="media.mediafile",
                verbose_name="Background",
            ),
        ),
        migrations.RunPython(migrate_cardface_backgrounds, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="cardface",
            name="background",
        ),
    ]
