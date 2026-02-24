"""OpenPay payment processor model."""

from __future__ import annotations

import contextlib
import hashlib
import hmac

import requests
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.payments.models.base import PaymentProcessor
from apps.sigils.fields import SigilShortAutoField


class OpenPayProcessor(PaymentProcessor):
    """Store OpenPay credentials."""

    SANDBOX_API_URL = "https://sandbox-api.openpay.mx/v1"
    PRODUCTION_API_URL = "https://api.openpay.mx/v1"

    profile_fields = ("merchant_id", "private_key", "public_key", "webhook_secret")
    verification_fields = (
        "merchant_id",
        "private_key",
        "public_key",
        "is_production",
        "webhook_secret",
    )

    merchant_id = SigilShortAutoField(max_length=100, blank=True)
    private_key = SigilShortAutoField(max_length=255, blank=True)
    public_key = SigilShortAutoField(max_length=255, blank=True)
    is_production = models.BooleanField(default=False)
    webhook_secret = SigilShortAutoField(max_length=255, blank=True)

    class Meta:
        """Model metadata."""

        verbose_name = _("OpenPay Processor")
        verbose_name_plural = _("OpenPay Processors")

    def get_api_base_url(self) -> str:
        """Return API base URL for the active environment."""

        return self.PRODUCTION_API_URL if self.is_production else self.SANDBOX_API_URL

    def build_api_url(self, path: str = "") -> str:
        """Build a merchant-scoped OpenPay endpoint URL."""

        path = path.strip("/")
        base = self.get_api_base_url()
        if path:
            return f"{base}/{self.merchant_id}/{path}"
        return f"{base}/{self.merchant_id}"

    def get_auth(self) -> tuple[str, str]:
        """Return HTTP basic auth tuple."""

        return (self.private_key, "")

    def is_sandbox(self) -> bool:
        """Return whether this processor points at OpenPay sandbox."""

        return not self.is_production

    def sign_webhook(self, payload: bytes | str, timestamp: str | None = None) -> str:
        """Sign a webhook payload using the configured webhook secret."""

        if not self.webhook_secret:
            raise ValueError("Webhook secret is not configured")
        if isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = payload
        message = b".".join([timestamp.encode("utf-8"), payload_bytes]) if timestamp else payload_bytes
        return hmac.new(
            self.webhook_secret.encode("utf-8"),
            message,
            hashlib.sha512,
        ).hexdigest()

    def use_production(self):
        """Switch to production and clear verification state."""

        self.is_production = True
        self._clear_verification()
        return self

    def use_sandbox(self):
        """Switch to sandbox and clear verification state."""

        self.is_production = False
        self._clear_verification()
        return self

    def set_environment(self, *, production: bool):
        """Set environment and clear verification state."""

        self.is_production = bool(production)
        self._clear_verification()
        return self

    def has_credentials(self) -> bool:
        """Return whether required credentials are present."""

        return all(getattr(self, field) for field in ("merchant_id", "private_key", "public_key"))

    def verify(self):
        """Verify OpenPay credentials against OpenPay API."""

        url = self.build_api_url("charges")
        response = None
        try:
            response = requests.get(url, auth=self.get_auth(), params={"limit": 1}, timeout=10)
        except requests.RequestException as exc:  # pragma: no cover - network failure
            self._clear_verification()
            if self.pk:
                self.save(update_fields=["verification_reference", "verified_on"])
            raise ValidationError(_("Unable to verify OpenPay credentials: %(error)s") % {"error": exc}) from exc
        try:
            if response.status_code != 200:
                self._clear_verification()
                if self.pk:
                    self.save(update_fields=["verification_reference", "verified_on"])
                raise ValidationError(_("Invalid OpenPay credentials"))
            try:
                payload = response.json() or {}
            except ValueError:
                payload = {}
            reference = ""
            if isinstance(payload, dict):
                reference = payload.get("status") or payload.get("name") or payload.get("id") or payload.get("description") or ""
            elif isinstance(payload, list) and payload:
                first = payload[0]
                if isinstance(first, dict):
                    reference = first.get("status") or first.get("id") or first.get("description") or ""
            self.verification_reference = str(reference) if reference else ""
            self.verified_on = timezone.now()
            self.save(update_fields=["verification_reference", "verified_on"])
            return True
        finally:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()
