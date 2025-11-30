from django.apps import AppConfig


class LocalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.locals"

    def ready(self):
        from .admin import patch_admin_favorites
        from .user_data import patch_admin_user_datum, patch_admin_user_data_views

        patch_admin_user_datum()
        patch_admin_user_data_views()
        patch_admin_favorites()
