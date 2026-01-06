from django.apps import AppConfig


class CountersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.counters"
    label = "counters"

    def ready(self):  # pragma: no cover - import side effects
        from .system import patch_admin_dashboard_rules_report_view

        patch_admin_dashboard_rules_report_view()
