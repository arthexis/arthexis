from django.apps import AppConfig
from django.db.models.signals import post_migrate


class FeaturesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.features"
    label = "features"
    verbose_name = "Suite Features"

    def ready(self):  # pragma: no cover - import for side effects
        from . import widgets  # noqa: F401
        from .loader import load_feature_seed_data

        post_migrate.connect(
            load_feature_seed_data,
            sender=self,
            dispatch_uid="features_load_feature_seed_data",
            weak=False,
        )
