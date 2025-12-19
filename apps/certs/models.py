from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.certs import services


class Certificate(models.Model):
    """Abstract base class for certificates."""

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
    class Meta:
        verbose_name = _("Certificate")
        verbose_name_plural = _("Certificates")
        ordering = ("name",)

    def provision(self, *, sudo: str = "sudo") -> str:
        """Generate or request this certificate based on its type."""

        certificate = self._specific_certificate

        if isinstance(certificate, CertbotCertificate):
            return certificate.request(sudo=sudo)
        if isinstance(certificate, SelfSignedCertificate):
            return certificate.generate(sudo=sudo)

        raise TypeError(f"Unsupported certificate type: {type(self).__name__}")

    def verify(self, *, sudo: str = "sudo") -> services.CertificateVerificationResult:
        """Verify certificate validity and filesystem alignment."""
        certificate_path = Path(self.certificate_path) if self.certificate_path else None
        certificate_key_path = Path(self.certificate_key_path) if self.certificate_key_path else None
        return services.verify_certificate(
            domain=self.domain,
            certificate_path=certificate_path,
            certificate_key_path=certificate_key_path,
            sudo=sudo,
        )

    @property
    def _specific_certificate(self) -> "CertificateBase":
        if isinstance(self, (CertbotCertificate, SelfSignedCertificate)):
            return self
        for attr in ("certbotcertificate", "selfsignedcertificate"):
            try:
                return getattr(self, attr)
            except (AttributeError, ObjectDoesNotExist):
                continue
        return self


class CertbotCertificate(CertificateBase):
    email = models.EmailField(blank=True)
    last_requested_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Certbot certificate")
        verbose_name_plural = _("Certbot certificates")

    def request(self, *, sudo: str = "sudo") -> str:
        """Trigger certbot for this certificate."""

        if not self.certificate_path:
            self.certificate_path = f"/etc/letsencrypt/live/{self.domain}/fullchain.pem"
        if not self.certificate_key_path:
            self.certificate_key_path = f"/etc/letsencrypt/live/{self.domain}/privkey.pem"

        message = services.request_certbot_certificate(
            domain=self.domain,
            email=self.email or None,
            certificate_path=self.certificate_file,
            certificate_key_path=self.certificate_key_file,
            sudo=sudo,
        )
        self.last_requested_at = timezone.now()
        self.last_message = message
        self.save(
            update_fields=[
                "certificate_path",
                "certificate_key_path",
                "last_requested_at",
                "last_message",
                "updated_at",
            ]
        )
        return message


class SelfSignedCertificate(CertificateBase):
    valid_days = models.PositiveIntegerField(default=365)
    key_length = models.PositiveIntegerField(default=2048)
    last_generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Self-signed certificate")
        verbose_name_plural = _("Self-signed certificates")

    def generate(self, *, sudo: str = "sudo") -> str:
        """Generate a self-signed certificate for this domain."""

        message = services.generate_self_signed_certificate(
            domain=self.domain,
            certificate_path=self.certificate_file,
            certificate_key_path=self.certificate_key_file,
            days_valid=self.valid_days,
            key_length=self.key_length,
            sudo=sudo,
        )
        self.last_generated_at = timezone.now()
        self.last_message = message
        self.save(
            update_fields=[
                "last_generated_at",
                "last_message",
                "updated_at",
            ]
        )
        return message
