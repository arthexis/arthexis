"""App configuration for Google Sheets integration."""

from django.apps import AppConfig


class GoogleSheetsConfig(AppConfig):
    """Register the Google Sheets integration under the /google route."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.google.sheets"
    label = "google"
    verbose_name = "Google"
