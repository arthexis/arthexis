from django.apps import AppConfig


class OdooConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.odoo"
    label = "odoo"

    def ready(self):  # pragma: no cover - admin registration side effect
        from . import admin_core  # noqa: F401
