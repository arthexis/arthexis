from django.apps import AppConfig


class ReleaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.release"
    verbose_name = "Releases"

    def ready(self):
        from apps.release import admin_views, task_compat  # noqa: F401

        # Permission ownership moved out of the former core AdminNotice model.
        admin_views.UPGRADE_CHECK_PERMISSION = "release.can_trigger_upgrade_checks"
