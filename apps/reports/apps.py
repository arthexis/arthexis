from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reports"
    verbose_name = "Reports"

    def ready(self):  # pragma: no cover - import side effects
        from .system import patch_admin_sql_report_view

        patch_admin_sql_report_view()
