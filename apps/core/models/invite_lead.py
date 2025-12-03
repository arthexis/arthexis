from django.db import models

from apps.leads.models import Lead


class InviteLead(Lead):
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
        verbose_name = "Invite Lead"
        verbose_name_plural = "Invite Leads"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.email
