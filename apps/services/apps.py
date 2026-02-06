from __future__ import annotations

import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate


logger = logging.getLogger(__name__)


class ServicesConfig(AppConfig):
    name = "apps.services"
    verbose_name = "Lifecycle Services"

    def ready(self) -> None:
        from . import signals

        post_migrate.connect(
            signals.refresh_lifecycle_service_config,
            sender=self,
            dispatch_uid="services.refresh_lifecycle_config",
        )
