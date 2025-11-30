from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CeleryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.celery"
    label = "celery_app"
    order = 1
    verbose_name = _("Celery")
