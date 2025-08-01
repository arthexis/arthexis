from django.apps import AppConfig


class OdooConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "crm.odoo"
    label = "odoo"
    verbose_name = "Relationship Managers"

    def ready(self):
        # Import admin registrations.
        from . import relationship_managers  # noqa: F401
