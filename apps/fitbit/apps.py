"""Application config for the temporary Fitbit migration app."""

from django.apps import AppConfig


class FitbitConfig(AppConfig):
    """Keep the legacy Fitbit app installed long enough to run cleanup migrations.

    Parameters:
        None.

    Returns:
        None.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fitbit"
    verbose_name = "Fitbit"
