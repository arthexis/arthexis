from django.apps import AppConfig


class XServerConfig(AppConfig):
    """Application config for X display server discovery support."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.xserver"
    verbose_name = "X Server"
