from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.leads.models import Lead


class LandingLead(Lead):
    landing = models.ForeignKey(
        "pages.Landing", on_delete=models.CASCADE, related_name="leads"
    )

    class Meta:
        verbose_name = _("Landing Lead")
        verbose_name_plural = _("Landing Leads")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.landing.label} ({self.path})"
