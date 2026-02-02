from django.apps import AppConfig


class LocalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.locals"

    def ready(self):
        from .admin import patch_admin_favorites
        from .user_data.admin_views import patch_admin_user_data_views
        from .user_data.transfer import patch_admin_import_export
        from .user_data.core import patch_admin_user_datum
        from .user_data import signals  # noqa: F401

        patch_admin_user_datum()
        patch_admin_import_export()
        patch_admin_user_data_views()
        patch_admin_favorites()
