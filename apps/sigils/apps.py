from django.apps import AppConfig
from django.db.models.signals import post_migrate


class SigilsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sigils"
    label = "sigils"

    def ready(self):  # pragma: no cover - Django hook
        from .loader import load_fixture_sigil_roots
        from .sigil_builder import generate_model_sigils, patch_admin_sigil_builder_view

        post_migrate.connect(
            generate_model_sigils,
            sender=self,
            dispatch_uid="sigils_generate_model_sigils",
        )
        post_migrate.connect(
            load_fixture_sigil_roots,
            sender=None,
            dispatch_uid="sigils_load_fixture_sigil_roots",
        )
        patch_admin_sigil_builder_view()
