from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


class SigilsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sigils"
    label = "sigils"
    verbose_name = _("Sigils")

    def ready(self):  # pragma: no cover - Django hook
        from .sigil_builder import generate_model_sigils, patch_admin_sigil_builder_view

        post_migrate.connect(generate_model_sigils, sender=self)
        patch_admin_sigil_builder_view()
