from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.certs import services

logger = logging.getLogger(__name__)


class Certificate(models.Model):
    """Abstract base class for certificates managed or referenced by Arthexis."""

    name = models.CharField(max_length=128, unique=True)
    domain = models.CharField(max_length=253)
    certificate_path = models.CharField(max_length=500)
    certificate_key_path = models.CharField(max_length=500)
    last_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ("name",)

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.name} ({self.domain})"

    @property
    def certificate_file(self) -> Path:
        return Path(self.certificate_path)

    @property
    def certificate_key_file(self) -> Path:
        return Path(self.certificate_key_path)


class CertificateBase(Certificate):
    """Certificate inventory shared by OCPP and other application features.

    Public-web ACME/Certbot lifecycle belongs to gway-web. Arthexis keeps this
    model for certificate inventory, verification, and application-owned
    certificates such as self-signed OCPP material.
    """

    expiration_date = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("Certificate")
        verbose_name_plural = _("Certificates")
        ordering = ("name",)

    def provision(self, *, sudo: str = "sudo") -> str:
        """Provision an application-owned certificate subtype."""
        certificate = self._specific_certificate
        if isinstance(certificate, SelfSignedCertificate):
            return certificate.generate(sudo=sudo)
        raise TypeError(f"Unsupported certificate type: {type(self).__name__}")

    def update_expiration_date(self, *, sudo: str = "sudo") -> datetime | None:
        """Read expiration from disk and persist it to the database."""
        if not self.certificate_path:
            if self.expiration_date is not None:
                self.expiration_date = None
                self.save(update_fields=["expiration_date", "updated_at"])
            return None
        certificate_path = Path(self.certificate_path)
        if not certificate_path.exists():
            if self.expiration_date is not None:
                self.expiration_date = None
                self.save(update_fields=["expiration_date", "updated_at"])
            return None
        expiration = services.get_certificate_expiration(
            certificate_path=certificate_path,
            sudo=sudo,
        )
        self.expiration_date = expiration
        self.save(update_fields=["expiration_date", "updated_at"])
        return expiration

    def is_due_for_renewal(self, *, now: datetime | None = None) -> bool:
        """Return True when the stored expiration is in the past."""
        if not self.expiration_date:
            return False
        current_time = now or timezone.now()
        return self.expiration_date <= current_time

    def renew(self, *, sudo: str = "sudo") -> str:
        """Renew an application-owned certificate and refresh its expiration."""
        message = self.provision(sudo=sudo)
        try:
            self.update_expiration_date(sudo=sudo)
        except RuntimeError:
            logger.exception(
                "Failed to refresh expiration_date after renewal for certificate %s",
                self.pk,
            )
        return message

    def verify(self, *, sudo: str = "sudo") -> services.CertificateVerificationResult:
        """Verify certificate validity and filesystem alignment."""
        certificate_path = (
            Path(self.certificate_path) if self.certificate_path else None
        )
        certificate_key_path = (
            Path(self.certificate_key_path) if self.certificate_key_path else None
        )
        return services.verify_certificate(
            domain=self.domain,
            certificate_path=certificate_path,
            certificate_key_path=certificate_key_path,
            sudo=sudo,
        )

    def verify_paths(
        self,
        *,
        sudo: str = "sudo",
    ) -> services.CertificateVerificationResult:
        """Backward-compatible alias for certificate path verification."""
        return self.verify(sudo=sudo)

    @property
    def _specific_certificate(self) -> CertificateBase:
        if isinstance(self, SelfSignedCertificate):
            return self
        try:
            return self.selfsignedcertificate
        except (AttributeError, ObjectDoesNotExist):
            return self


class SelfSignedCertificate(CertificateBase):
    """Application-owned self-signed certificate, useful for local/OCPP PKI."""

    valid_days = models.PositiveIntegerField(default=365)
    key_length = models.PositiveIntegerField(default=2048)
    last_generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Self-signed certificate")
        verbose_name_plural = _("Self-signed certificates")

    def generate(
        self,
        *,
        sudo: str = "sudo",
        subject_alt_names: list[str] | None = None,
    ) -> str:
        """Generate a self-signed certificate for this domain."""
        message = services.generate_self_signed_certificate(
            domain=self.domain,
            certificate_path=self.certificate_file,
            certificate_key_path=self.certificate_key_file,
            days_valid=self.valid_days,
            key_length=self.key_length,
            subject_alt_names=subject_alt_names,
            sudo=sudo,
        )
        try:
            self.expiration_date = services.get_certificate_expiration(
                certificate_path=self.certificate_file,
                sudo=sudo,
            )
        except RuntimeError:
            self.expiration_date = None
        self.last_generated_at = timezone.now()
        self.last_message = message
        self.save(
            update_fields=[
                "expiration_date",
                "last_generated_at",
                "last_message",
                "updated_at",
            ]
        )
        return message
