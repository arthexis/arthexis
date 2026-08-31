from __future__ import annotations

from .base import *


class CertificateRequest(Entity):
    """Persist incoming certificate signing requests from charge points."""

    ACTION_SIGN = "SignCertificate"
    ACTION_15118 = "Get15118EVCertificate"
    ACTION_CHOICES = (
        (ACTION_SIGN, "Sign Certificate"),
        (ACTION_15118, "Get 15118 EV Certificate"),
    )
    STATUS_PENDING = "Pending"
    STATUS_ACCEPTED = "Accepted"
    STATUS_REJECTED = "Rejected"
    STATUS_ERROR = "Error"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_ERROR, "Error"),
    )

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="certificate_requests",
    )
    action = models.CharField(max_length=80, choices=ACTION_CHOICES)
    certificate_type = models.CharField(max_length=80, blank=True, default="")
    csr = models.TextField(blank=True, default="")
    signed_certificate = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    status_info = models.TextField(blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        verbose_name = _("Certificate request")
        verbose_name_plural = _("Certificate requests")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}: {self.action}"


class CertificateStatusCheck(Entity):
    """Persist certificate status checks from charge points."""

    STATUS_PENDING = "Pending"
    STATUS_ACCEPTED = "Accepted"
    STATUS_REJECTED = "Rejected"
    STATUS_ERROR = "Error"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_ERROR, "Error"),
    )

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="certificate_status_checks",
    )
    certificate_hash_data = models.JSONField(default=dict, blank=True)
    ocsp_result = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    status_info = models.TextField(blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        verbose_name = _("Certificate status check")
        verbose_name_plural = _("Certificate status checks")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}: certificate status"


class CertificateOperation(Entity):
    """Track CSMS-initiated certificate operations."""

    ACTION_INSTALL = "InstallCertificate"
    ACTION_DELETE = "DeleteCertificate"
    ACTION_SIGNED = "CertificateSigned"
    ACTION_LIST = "GetInstalledCertificateIds"
    ACTION_CHOICES = (
        (ACTION_INSTALL, "Install certificate"),
        (ACTION_DELETE, "Delete certificate"),
        (ACTION_SIGNED, "Certificate signed"),
        (ACTION_LIST, "Get installed certificate ids"),
    )
    STATUS_PENDING = "Pending"
    STATUS_ACCEPTED = "Accepted"
    STATUS_REJECTED = "Rejected"
    STATUS_ERROR = "Error"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_ERROR, "Error"),
    )

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="certificate_operations",
    )
    action = models.CharField(max_length=80, choices=ACTION_CHOICES)
    certificate_type = models.CharField(max_length=80, blank=True, default="")
    certificate_hash_data = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    status_info = models.TextField(blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=120, blank=True, default="")
    error_description = models.TextField(blank=True, default="")
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        verbose_name = _("Certificate operation")
        verbose_name_plural = _("Certificate operations")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}: {self.action}"


class InstalledCertificate(Entity):
    """Track installed certificates per charge point."""

    STATUS_PENDING = "Pending"
    STATUS_INSTALLED = "Installed"
    STATUS_REJECTED = "Rejected"
    STATUS_DELETE_PENDING = "DeletePending"
    STATUS_DELETED = "Deleted"
    STATUS_ERROR = "Error"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_INSTALLED, "Installed"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_DELETE_PENDING, "Delete pending"),
        (STATUS_DELETED, "Deleted"),
        (STATUS_ERROR, "Error"),
    )

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="installed_certificates",
    )
    certificate_type = models.CharField(max_length=80, blank=True, default="")
    certificate = models.TextField(blank=True, default="")
    certificate_hash_data = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    last_action = models.CharField(max_length=80, blank=True, default="")
    installed_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-pk"]
        verbose_name = _("Installed certificate")
        verbose_name_plural = _("Installed certificates")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.certificate_type or "Certificate"
        return f"{self.charger}: {label}"


class TrustAnchor(Entity):
    """Persist trust anchor certificates for 15118 and CSMS CA chains."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="trust_anchors",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=120, blank=True, default="")
    certificate = models.TextField(blank=True, default="")
    certificate_hash_data = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "pk"]
        verbose_name = _("Trust anchor")
        verbose_name_plural = _("Trust anchors")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.name or "Trust anchor"
        target = self.charger or "Global"
        return f"{target}: {label}"
