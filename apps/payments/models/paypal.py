"""PayPal payment processor model."""

from __future__ import annotations

import contextlib

import requests
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.payments.models.base import PaymentProcessor
from apps.sigils.fields import SigilShortAutoField


class PayPalProcessor(PaymentProcessor):
    """Store PayPal REST credentials."""

    PAYPAL_SANDBOX_API_URL = "https://api-m.sandbox.paypal.com"
    PAYPAL_PRODUCTION_API_URL = "https://api-m.paypal.com"

    profile_fields = ("client_id", "client_secret", "webhook_id")
    verification_fields = (
        "client_id",
        "client_secret",
        "is_production",
        "webhook_id",
    )

    client_id = SigilShortAutoField(max_length=255, blank=True)
    client_secret = SigilShortAutoField(max_length=255, blank=True)
    webhook_id = SigilShortAutoField(max_length=255, blank=True)
    is_production = models.BooleanField(default=False)

    class Meta:
        """Model metadata."""

        verbose_name = _("PayPal Processor")
        verbose_name_plural = _("PayPal Processors")

    def get_api_base_url(self) -> str:
        """Return API base URL for the active environment."""

        return self.PAYPAL_PRODUCTION_API_URL if self.is_production else self.PAYPAL_SANDBOX_API_URL

    def get_auth(self) -> tuple[str, str]:
        """Return HTTP basic auth tuple."""

        return (self.client_id, self.client_secret)

    def has_credentials(self) -> bool:
        """Return whether required credentials are present."""

        return all(getattr(self, field) for field in ("client_id", "client_secret"))

    def verify(self):
        """Verify PayPal credentials against PayPal API."""

        response = None
        url = f"{self.get_api_base_url()}/v1/oauth2/token"
        try:
            response = requests.post(
                url,
                auth=self.get_auth(),
                data={"grant_type": "client_credentials"},
                timeout=10,
            )
        except requests.RequestException as exc:  # pragma: no cover - network failure
            self._clear_verification()
            if self.pk:
                self.save(update_fields=["verification_reference", "verified_on"])
            raise ValidationError(_("Unable to verify PayPal credentials: %(error)s") % {"error": exc}) from exc
        try:
            if response.status_code != 200:
                self._clear_verification()
                if self.pk:
                    self.save(update_fields=["verification_reference", "verified_on"])
                raise ValidationError(_("Invalid PayPal credentials"))
            try:
                payload = response.json() or {}
            except ValueError:
                payload = {}
            scope = ""
            if isinstance(payload, dict):
                scope = payload.get("scope") or payload.get("access_token") or ""
            self.verification_reference = f"PayPal: {scope}" if scope else "PayPal"
            self.verified_on = timezone.now()
            self.save(update_fields=["verification_reference", "verified_on"])
            return True
        finally:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()
