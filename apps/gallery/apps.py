from django.apps import AppConfig
from django.db.models.signals import post_migrate


class GalleryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.gallery"
    verbose_name = "Gallery"

    def ready(self) -> None:
        from .defaults import ensure_default_gallery_categories

        post_migrate.connect(
            ensure_default_gallery_categories,
            sender=self,
            dispatch_uid="gallery.default_categories_post_migrate",
        )
