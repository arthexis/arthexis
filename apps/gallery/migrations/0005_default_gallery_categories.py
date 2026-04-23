from django.db import migrations


DEFAULT_GALLERY_CATEGORIES = (
    ("artist", "Artist"),
    ("designer", "Designer"),
    ("developer", "Developer"),
    ("template", "Template"),
)


def ensure_default_gallery_categories(apps, schema_editor):
    GalleryCategory = apps.get_model("gallery", "GalleryCategory")
    for slug, name in DEFAULT_GALLERY_CATEGORIES:
        GalleryCategory.objects.update_or_create(
            slug=slug,
            defaults={"name": name},
        )


def remove_default_gallery_categories(apps, schema_editor):
    GalleryCategory = apps.get_model("gallery", "GalleryCategory")
    GalleryCategory.objects.filter(slug__in=[slug for slug, _ in DEFAULT_GALLERY_CATEGORIES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("gallery", "0004_galleryimage_shared_with_users"),
    ]

    operations = [
        migrations.RunPython(ensure_default_gallery_categories, remove_default_gallery_categories),
    ]
