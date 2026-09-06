from django.conf import settings
from django.db import models

from .lead_base import LeadBase


class InviteLead(LeadBase):
    # Preserve the reverse accessor produced while this model lived in ``core``.
    assign_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="core_invitelead_assignments",
    )
    email = models.EmailField()
    comment = models.TextField(blank=True)
    sent_on = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    mac_address = models.CharField(max_length=17, blank=True)
    sent_via_outbox = models.ForeignKey(
        "emails.EmailOutbox",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invite_leads",
    )

    class Meta:
        db_table = "core_invitelead"
        verbose_name = "Invite Lead"
        verbose_name_plural = "Invite Leads"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return the invite lead email address."""
        return self.email
