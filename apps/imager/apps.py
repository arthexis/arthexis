from django.apps import AppConfig


class ImagerConfig(AppConfig):
    """Migration-only compatibility shell for the retired imager app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.imager"
    label = "imager"
    verbose_name = "Imager (retired)"
