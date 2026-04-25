# APP_STRUCTURE: backend-only (intentionally omits views.py, urls.py, and routes.py)
from django.apps import AppConfig


class LibraryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.library"
    verbose_name = "Library"
