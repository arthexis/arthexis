from .models import GalleryCategory

DEFAULT_GALLERY_CATEGORIES = (
    ("artist", "Artist"),
    ("designer", "Designer"),
    ("developer", "Developer"),
    ("template", "Template"),
)


def ensure_default_gallery_categories(**kwargs) -> None:
    for slug, name in DEFAULT_GALLERY_CATEGORIES:
        GalleryCategory.objects.update_or_create(
            slug=slug,
            defaults={"name": name},
        )
