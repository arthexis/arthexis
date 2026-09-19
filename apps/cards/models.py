from django.conf import settings
from django.db import models


class CardCredential(models.Model):
    external_id = models.CharField(max_length=128, unique=True)
    label = models.CharField(max_length=120, blank=True)
    ocpp_id_tag = models.CharField(max_length=20, blank=True)
    active = models.BooleanField(default=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="card_credentials",
    )
    account = models.ForeignKey(
        "energy.CustomerAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="card_credentials",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("external_id",)

    def __str__(self):
        return self.label or self.external_id


class AuthorizationAttempt(models.Model):
    card = models.ForeignKey(
        CardCredential, on_delete=models.SET_NULL, null=True, blank=True
    )
    presented_id = models.CharField(max_length=128)
    accepted = models.BooleanField(default=False)
    reason = models.CharField(max_length=240, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-occurred_at",)
