from django.apps import AppConfig


class ReleaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.release"
    verbose_name = "Releases"

    def ready(self):
        from apps.release import task_compat  # noqa: F401
