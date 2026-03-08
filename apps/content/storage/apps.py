from django.apps import AppConfig


class MediaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.content.storage"
    label = "content_storage"
    verbose_name = "Media Storage"
