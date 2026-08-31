from django.apps import AppConfig



# APP_STRUCTURE: backend-only (intentionally omits views.py, urls.py, and routes.py)
class SerialBridgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.serialbridge"
