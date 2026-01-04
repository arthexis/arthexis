from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class FTPConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ftp"
    verbose_name = _("FTP")
