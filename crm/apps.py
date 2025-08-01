from django.apps import AppConfig


class CrmConfig(AppConfig):
    """Top-level CRM application used to group CRM-related sub-apps."""

    # This app currently provides minimal configuration but serves as the
    # parent package for CRM functionality such as the Odoo integration.
    default_auto_field = "django.db.models.BigAutoField"
    name = "crm"
