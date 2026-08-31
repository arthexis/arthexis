from django.apps import AppConfig


# APP_STRUCTURE: backend-only (intentionally omits views.py, urls.py, and routes.py)
class PrintersConfig(AppConfig):
    """Default app configuration for scaffolded local app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.printers"
    label = "printers"
    verbose_name = "Printers"
