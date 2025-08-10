from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class EmailsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "emails"
    verbose_name = _("Post Office")

    def ready(self):  # pragma: no cover - runtime configuration
        """Group EmailPattern models under the external Post Office app."""
        from django.apps import apps

        from .models import EmailPattern

        # Move the model from the ``emails`` app into ``post_office`` so the
        # admin only shows a single Post Office section.
        post_office_config = apps.get_app_config("post_office")
        emails_config = apps.get_app_config("emails")

        emails_config.models.pop(EmailPattern.__name__.lower(), None)
        EmailPattern._meta.app_label = "post_office"
        post_office_config.models[EmailPattern.__name__.lower()] = EmailPattern

        from django.contrib import admin
        from .admin import EmailPatternAdmin

        admin.site.register(EmailPattern, EmailPatternAdmin)

        from django.conf import settings
        from django.urls import clear_url_caches
        from importlib import import_module, reload

        clear_url_caches()
        reload(import_module(settings.ROOT_URLCONF))
