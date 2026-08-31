from __future__ import annotations

from .base import *
from .cp_firmware import CPFirmware

class CPFirmwareDeployment(Entity):
    """Track firmware rollout attempts for specific charge points."""

    TERMINAL_STATUSES = {
        "Installed",
        "InstallationFailed",
        "DownloadFailed",
        "Published",
        "PublishFailed",
    }

    firmware = models.ForeignKey(
        CPFirmware,
        on_delete=models.CASCADE,
        related_name="deployments",
        verbose_name=_("Firmware"),
    )
    charger = models.ForeignKey(
        "Charger",
        on_delete=models.PROTECT,
        related_name="firmware_deployments",
        verbose_name=_("Charge point"),
    )
    node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="firmware_deployments",
        verbose_name=_("Node"),
    )
    ocpp_message_id = models.CharField(
        _("OCPP message ID"), max_length=64, blank=True
    )
    status = models.CharField(_("Status"), max_length=32, blank=True)
    status_info = models.CharField(_("Status details"), max_length=255, blank=True)
    status_timestamp = models.DateTimeField(_("Status timestamp"), null=True, blank=True)
    requested_at = models.DateTimeField(_("Requested at"), auto_now_add=True)
    completed_at = models.DateTimeField(_("Completed at"), null=True, blank=True)
    retrieve_date = models.DateTimeField(_("Retrieve date"), null=True, blank=True)
    retry_count = models.PositiveIntegerField(_("Retries"), default=0)
    retry_interval = models.PositiveIntegerField(
        _("Retry interval (seconds)"), default=0
    )
    download_token = models.CharField(_("Download token"), max_length=64, blank=True)
    download_token_expires_at = models.DateTimeField(
        _("Token expires at"), null=True, blank=True
    )
    downloaded_at = models.DateTimeField(_("Downloaded at"), null=True, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = _("CP Firmware Deployment")
        verbose_name_plural = _("CP Firmware Deployments")
        indexes = [
            models.Index(fields=["ocpp_message_id"]),
            models.Index(fields=["download_token"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.firmware} → {self.charger}" if self.pk else "Firmware Deployment"

    def issue_download_token(self, *, lifetime: timedelta | None = None) -> str:
        if lifetime is None:
            lifetime = timedelta(hours=1)
        deadline = timezone.now() + lifetime
        token = secrets.token_urlsafe(24)
        while type(self).all_objects.filter(download_token=token).exists():
            token = secrets.token_urlsafe(24)
        self.download_token = token
        self.download_token_expires_at = deadline
        self.save(
            update_fields=["download_token", "download_token_expires_at", "updated_at"]
        )
        return token

    def mark_status(
        self,
        status: str,
        info: str = "",
        timestamp: datetime | None = None,
        *,
        response: dict | None = None,
    ) -> None:
        timestamp_value = timestamp or timezone.now()
        self.status = status
        self.status_info = info
        self.status_timestamp = timestamp_value
        if response is not None:
            self.response_payload = response
        if status in self.TERMINAL_STATUSES and not self.completed_at:
            self.completed_at = timezone.now()

        update_fields = [
            "status",
            "status_info",
            "status_timestamp",
            "response_payload",
            "updated_at",
        ]
        if self.completed_at:
            update_fields.append("completed_at")
        if self.downloaded_at:
            update_fields.append("downloaded_at")

        self.save(update_fields=update_fields)

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES and bool(self.completed_at)
