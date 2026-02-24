"""Stripe payment processor model."""

from __future__ import annotations

import contextlib

import requests
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.payments.models.base import PaymentProcessor
from apps.sigils.fields import SigilShortAutoField


class StripeProcessor(PaymentProcessor):
    """Store Stripe credentials."""

    STRIPE_API_URL = "https://api.stripe.com"

    profile_fields = ("secret_key", "publishable_key", "webhook_secret")
    verification_fields = (
        "secret_key",
        "publishable_key",
        "webhook_secret",
        "is_production",
    )

    secret_key = SigilShortAutoField(max_length=255, blank=True)
    publishable_key = SigilShortAutoField(max_length=255, blank=True)
    webhook_secret = SigilShortAutoField(max_length=255, blank=True)
    is_production = models.BooleanField(default=False)

    class Meta:
        """Model metadata."""

        verbose_name = _("Stripe Processor")
        verbose_name_plural = _("Stripe Processors")

    def get_api_base_url(self) -> str:
        """Return Stripe API base URL."""

        return self.STRIPE_API_URL

    def get_headers(self) -> dict[str, str]:
        """Return authenticated request headers."""

        return {"Authorization": f"Bearer {self.secret_key}"}

    def has_credentials(self) -> bool:
        """Return whether required credentials are present."""

        return all(getattr(self, field) for field in ("secret_key", "publishable_key"))

    def verify(self):
        """Verify Stripe credentials against Stripe API."""

        response = None
        url = f"{self.get_api_base_url()}/v1/account"
        try:
            response = requests.get(url, headers=self.get_headers(), timeout=10)
        except requests.RequestException as exc:  # pragma: no cover - network failure
            self._clear_verification()
            if self.pk:
                self.save(update_fields=["verification_reference", "verified_on"])
            raise ValidationError(_("Unable to verify Stripe credentials: %(error)s") % {"error": exc}) from exc
        try:
            if response.status_code != 200:
                self._clear_verification()
                if self.pk:
                    self.save(update_fields=["verification_reference", "verified_on"])
                raise ValidationError(_("Invalid Stripe credentials"))
            try:
                payload = response.json() or {}
            except ValueError:
                payload = {}
            reference = ""
            if isinstance(payload, dict):
                reference = payload.get("id") or payload.get("email") or payload.get("object") or ""
            self.verification_reference = f"Stripe: {reference}" if reference else "Stripe"
            self.verified_on = timezone.now()
            self.save(update_fields=["verification_reference", "verified_on"])
            return True
        finally:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()
