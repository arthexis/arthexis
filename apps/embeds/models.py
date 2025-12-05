from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.leads.models import Lead


class EmbedLead(Lead):
    target_url = models.TextField(blank=True)
    share_referer = models.CharField(max_length=150, blank=True)

    class Meta:
        verbose_name = _("Embed Lead")
        verbose_name_plural = _("Embed Leads")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.target_url or self.path


__all__ = ["EmbedLead"]
