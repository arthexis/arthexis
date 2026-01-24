from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class EmailBridge(Entity):
    """Pair an Email inbox and outbox into a single configuration."""

    name = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Optional label for this inbox/outbox bridge."),
    )
    inbox = models.OneToOneField(
        "emails.EmailInbox",
        on_delete=models.PROTECT,
        related_name="bridge",
        help_text=_("Inbound mailbox for this bridge."),
    )
    outbox = models.OneToOneField(
        "emails.EmailOutbox",
        on_delete=models.PROTECT,
        related_name="bridge",
        help_text=_("Outbound mailbox for this bridge."),
    )

    class Meta:
        verbose_name = _("Email Bridge")
        verbose_name_plural = _("Email Bridges")
        db_table = "emails_emailbridge"
        ordering = ["name", "pk"]

    def __str__(self) -> str:
        name = (self.name or "").strip()
        if name:
            return name
        return f"{self.inbox} ↔ {self.outbox}"


__all__ = ["EmailBridge"]
