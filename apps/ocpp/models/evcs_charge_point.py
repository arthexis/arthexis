from __future__ import annotations

from .base import *
from .charger import Charger

class EVCSChargePointManager(EntityManager):
    def get_queryset(self):
        return super().get_queryset().filter(connector_id__isnull=True)

class EVCSChargePoint(Charger):
    objects = EVCSChargePointManager()

    class Meta:
        proxy = True
        verbose_name = _("EVCS Charge Point")
        verbose_name_plural = _("EVCS Charge Points")
