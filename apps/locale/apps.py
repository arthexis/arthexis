from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LocaleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.locale"
    verbose_name = _("Locales")
