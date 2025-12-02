from django.conf import settings
from django.core.validators import validate_ipv46_address
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class Lead(Entity):
    """Common request lead information."""

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        ASSIGNED = "assigned", _("Assigned")
        CLOSED = "closed", _("Closed")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    path = models.TextField(blank=True)
    referer = models.TextField(blank=True)
    user_agent = models.TextField(blank=True)
    ip_address = models.CharField(
        max_length=45,
        blank=True,
        validators=[validate_ipv46_address],
    )
    created_on = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    assign_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_assignments",
    )

    class Meta:
        abstract = True
