from django.apps import AppConfig


# APP_STRUCTURE: backend-only (intentionally omits views.py, urls.py, and routes.py)
class PrintersConfig(AppConfig):
    """QR label rendering and local printer utilities."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.printers"
    label = "printers"
    verbose_name = "Printers"
