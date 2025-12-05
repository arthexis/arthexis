"""Payment processors."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
from typing import Iterable

import requests
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.sigils.fields import SigilShortAutoField


class PaymentProcessor(Entity):
    """Abstract base for global payment processors."""

    verified_on = models.DateTimeField(null=True, blank=True)
    verification_reference = models.CharField(max_length=255, blank=True, editable=False)
    class Meta:
        abstract = True
        verbose_name = _("Payment Processor")
        verbose_name_plural = _("Payment Processors")

    verification_fields: Iterable[str] = ()

    def _clear_verification(self):
        self.verified_on = None
        self.verification_reference = ""

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = type(self).all_objects.get(pk=self.pk)
            except type(self).DoesNotExist:
                old = None
            if old is not None:
                for field in self.verification_fields:
                    if getattr(old, field, None) != getattr(self, field, None):
                        self._clear_verification()
                        break
        super().save(*args, **kwargs)

    @property
    def is_verified(self):
        return self.verified_on is not None

    def verify(self):  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def identifier(self) -> str:
        reference = (self.verification_reference or "").strip()
        if reference:
            return reference
        if self.pk:
            return f"{self._meta.verbose_name} #{self.pk}"
        return str(self._meta.verbose_name)

    def __str__(self):  # pragma: no cover - presentation
        return self.identifier()


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
        verbose_name = _("OpenPay Processor")
        verbose_name_plural = _("OpenPay Processors")
    def get_api_base_url(self) -> str:
        return self.PRODUCTION_API_URL if self.is_production else self.SANDBOX_API_URL

    def build_api_url(self, path: str = "") -> str:
        path = path.strip("/")
        base = self.get_api_base_url()
        if path:
            return f"{base}/{self.merchant_id}/{path}"
        return f"{base}/{self.merchant_id}"

    def get_auth(self) -> tuple[str, str]:
        return (self.private_key, "")

    def is_sandbox(self) -> bool:
        return not self.is_production

    def sign_webhook(self, payload: bytes | str, timestamp: str | None = None) -> str:
        if not self.webhook_secret:
            raise ValueError("Webhook secret is not configured")
        if isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = payload
        if timestamp:
            message = b".".join([timestamp.encode("utf-8"), payload_bytes])
        else:
            message = payload_bytes
        return hmac.new(
            self.webhook_secret.encode("utf-8"),
            message,
            hashlib.sha512,
        ).hexdigest()

    def use_production(self):
        self.is_production = True
        self._clear_verification()
        return self

    def use_sandbox(self):
        self.is_production = False
        self._clear_verification()
        return self

    def set_environment(self, *, production: bool):
        self.is_production = bool(production)
        self._clear_verification()
        return self

    def has_credentials(self) -> bool:
        return all(getattr(self, field) for field in ("merchant_id", "private_key", "public_key"))

    def verify(self):
        url = self.build_api_url("charges")
        response = None
        try:
            response = requests.get(
                url,
                auth=self.get_auth(),
                params={"limit": 1},
                timeout=10,
            )
        except requests.RequestException as exc:  # pragma: no cover - network failure
            self._clear_verification()
            if self.pk:
                self.save(update_fields=["verification_reference", "verified_on"])
            raise ValidationError(
                _("Unable to verify OpenPay credentials: %(error)s")
                % {"error": exc}
            ) from exc
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
                reference = (
                    payload.get("status")
                    or payload.get("name")
                    or payload.get("id")
                    or payload.get("description")
                    or ""
                )
            elif isinstance(payload, list) and payload:
                first = payload[0]
                if isinstance(first, dict):
                    reference = (
                        first.get("status")
                        or first.get("id")
                        or first.get("description")
                        or ""
                    )
            self.verification_reference = str(reference) if reference else ""
            self.verified_on = timezone.now()
            self.save(update_fields=["verification_reference", "verified_on"])
            return True
        finally:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()


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
        verbose_name = _("PayPal Processor")
        verbose_name_plural = _("PayPal Processors")
    def get_api_base_url(self) -> str:
        return (
            self.PAYPAL_PRODUCTION_API_URL
            if self.is_production
            else self.PAYPAL_SANDBOX_API_URL
        )

    def get_auth(self) -> tuple[str, str]:
        return (self.client_id, self.client_secret)

    def has_credentials(self) -> bool:
        return all(getattr(self, field) for field in ("client_id", "client_secret"))

    def verify(self):
        url = f"{self.get_api_base_url()}/v1/oauth2/token"
        response = None
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
            raise ValidationError(
                _("Unable to verify PayPal credentials: %(error)s")
                % {"error": exc}
            ) from exc
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
        verbose_name = _("Stripe Processor")
        verbose_name_plural = _("Stripe Processors")
    def get_api_base_url(self) -> str:
        return self.STRIPE_API_URL

    def get_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.secret_key}"}

    def has_credentials(self) -> bool:
        return all(
            getattr(self, field) for field in ("secret_key", "publishable_key")
        )

    def verify(self):
        url = f"{self.get_api_base_url()}/v1/account"
        response = None
        try:
            response = requests.get(
                url,
                headers=self.get_headers(),
                timeout=10,
            )
        except requests.RequestException as exc:  # pragma: no cover - network failure
            self._clear_verification()
            if self.pk:
                self.save(update_fields=["verification_reference", "verified_on"])
            raise ValidationError(
                _("Unable to verify Stripe credentials: %(error)s")
                % {"error": exc}
            ) from exc
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
                reference = (
                    payload.get("id")
                    or payload.get("email")
                    or payload.get("object")
                    or ""
                )
            self.verification_reference = (
                f"Stripe: {reference}" if reference else "Stripe"
            )
            self.verified_on = timezone.now()
            self.save(update_fields=["verification_reference", "verified_on"])
            return True
        finally:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()
