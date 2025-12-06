from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AwsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.aws"
    verbose_name = _("AWS")
