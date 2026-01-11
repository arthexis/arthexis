from importlib import import_module, util

from django.apps import AppConfig, apps


class WidgetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.widgets"
    label = "widgets"
    verbose_name = "Widgets"

    def ready(self) -> None:
        for app_config in apps.get_app_configs():
            module_name = f"{app_config.name}.widgets"
            if util.find_spec(module_name):
                import_module(module_name)

        # Import widgets modules so registrations are loaded early.
