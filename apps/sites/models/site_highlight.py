from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class SiteHighlight(models.Model):
    """Highlight message shown on the public site."""

    title = models.CharField(max_length=120)
    highlight_date = models.DateField(
        help_text=_("Date shown with this highlight."),
    )
    story = models.TextField(
        help_text=_("Short highlight story. URLs are auto-linked on the public site."),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Whether this highlight is eligible to appear on the public site."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-highlight_date", "-created_at", "-pk")
        verbose_name = _("Site Highlight")
        verbose_name_plural = _("Site Highlights")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.highlight_date}: {self.title}"
