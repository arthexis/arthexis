from __future__ import annotations

import fnmatch
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.entity import Entity, EntityManager
from nodes.models import Node
from ocpp.forwarder import forwarder

from .forwarding import (
    attempt_forwarding_probe,
    load_local_node_credentials,
    send_forwarding_metadata,
)


OCPP_FORWARDING_MESSAGES: Sequence[str] = (
    "Authorize",
    "BootNotification",
    "DataTransfer",
    "DiagnosticsStatusNotification",
    "FirmwareStatusNotification",
    "Heartbeat",
    "MeterValues",
    "StartTransaction",
    "StatusNotification",
    "StopTransaction",
    "ClearedChargingLimit",
    "CostUpdated",
    "Get15118EVCertificate",
    "GetCertificateStatus",
    "LogStatusNotification",
    "NotifyChargingLimit",
    "NotifyCustomerInformation",
    "NotifyDisplayMessages",
    "NotifyEVChargingNeeds",
    "NotifyEVChargingSchedule",
    "NotifyEvent",
    "NotifyMonitoringReport",
    "NotifyReport",
    "PublishFirmwareStatusNotification",
    "ReportChargingProfiles",
    "ReservationStatusUpdate",
    "SecurityEventNotification",
    "SignCertificate",
    "TransactionEvent",
)


def default_forwarded_messages() -> list[str]:
    return list(OCPP_FORWARDING_MESSAGES)


def media_bucket_slug() -> str:
    return uuid.uuid4().hex


def media_file_path(instance: "MediaFile", filename: str) -> str:
    bucket_slug = instance.bucket.slug or "bucket"
    return f"protocols/buckets/{bucket_slug}/{Path(filename).name}"


class MediaBucket(Entity):
    name = models.CharField(_("Name"), max_length=100, blank=True, default="")
    slug = models.SlugField(
        _("Upload Path"), max_length=32, default=media_bucket_slug, unique=True
    )
    allowed_patterns = models.TextField(
        _("Allowed file patterns"),
        blank=True,
        default="",
        help_text=_("Newline-separated glob patterns (for example, *.zip or *.log)."),
    )
    max_bytes = models.BigIntegerField(
        _("Maximum size (bytes)"),
        null=True,
        blank=True,
        help_text=_("Reject uploads that exceed this limit."),
    )
    expires_at = models.DateTimeField(
        _("Accept uploads until"),
        null=True,
        blank=True,
        help_text=_("Stop accepting uploads after this timestamp."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Media Bucket")
        verbose_name_plural = _("Media Buckets")
        ordering = ("-created_at",)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name or self.slug

    @property
    def patterns(self) -> list[str]:
        return [value.strip() for value in self.allowed_patterns.splitlines() if value.strip()]

    def is_expired(self, *, reference: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        reference_time = reference or timezone.now()
        return self.expires_at <= reference_time

    def allows_filename(self, filename: str) -> bool:
        if not filename:
            return False
        patterns = self.patterns
        if not patterns:
            return True
        name = Path(filename).name
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)

    def allows_size(self, size: int) -> bool:
        if not size:
            return True
        if self.max_bytes is None:
            return True
        return size <= self.max_bytes

    def relative_upload_path(self) -> str:
        return f"media/{self.slug}/"


class MediaFile(Entity):
    bucket = models.ForeignKey(
        MediaBucket, on_delete=models.CASCADE, related_name="files", verbose_name=_("Bucket")
    )
    file = models.FileField(upload_to=media_file_path)
    original_name = models.CharField(_("Original name"), max_length=255, blank=True, default="")
    content_type = models.CharField(_("Content type"), max_length=255, blank=True, default="")
    size = models.BigIntegerField(_("Size (bytes)"), default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Media File")
        verbose_name_plural = _("Media Files")
        ordering = ("-uploaded_at", "pk")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.original_name or Path(self.file.name).name

    def save(self, *args, **kwargs):
        if self.file and not self.original_name:
            self.original_name = Path(self.file.name).name
        if self.file and not self.size:
            self.size = getattr(self.file, "size", 0) or 0
        if self.file and not self.content_type:
            self.content_type = getattr(self.file, "content_type", "") or ""
        super().save(*args, **kwargs)


class CPForwarderManager(EntityManager):
    """Manager adding helpers for charge point forwarders."""

    def sync_forwarding_targets(self) -> None:
        for forwarder in self.all():
            forwarder.sync_chargers(apply_sessions=False)

    def update_running_state(self, active_target_ids: Iterable[int]) -> None:
        active = set(active_target_ids)
        for forwarder in self.all():
            forwarder.set_running_state(forwarder.target_node_id in active)


class CPForwarder(Entity):
    """Configuration for forwarding local charge point traffic to a remote node."""

    name = models.CharField(
        _("Name"),
        max_length=100,
        blank=True,
        default="",
        help_text=_("Optional label used when listing this forwarder."),
    )
    source_node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        related_name="outgoing_forwarders",
        null=True,
        blank=True,
        help_text=_("Node providing the original charge point connection."),
    )
    target_node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="incoming_forwarders",
        help_text=_("Remote node that will receive forwarded sessions."),
    )
    enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Enable to forward eligible charge points to the remote node. "
            "Charge points must also have Export transactions enabled."
        ),
    )
    forwarded_messages = models.JSONField(
        default=default_forwarded_messages,
        blank=True,
        help_text=_(
            "Select the OCPP messages that should be forwarded to the remote node."
        ),
    )
    is_running = models.BooleanField(
        default=False,
        editable=False,
        help_text=_("Indicates whether an active forwarding websocket is connected."),
    )
    last_forwarded_at = models.DateTimeField(
        _("Last forwarded at"),
        null=True,
        blank=True,
        help_text=_("Timestamp of the most recent forwarding activity."),
    )
    last_status = models.CharField(
        _("Last status"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Summary of the last forwarding synchronization."),
    )
    last_error = models.CharField(
        _("Last error"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Most recent error encountered while configuring forwarding."),
    )
    last_synced_at = models.DateTimeField(
        _("Last synced at"),
        null=True,
        blank=True,
        help_text=_("When the forwarder last attempted to apply its configuration."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CPForwarderManager()

    class Meta:
        verbose_name = _("CP Forwarder")
        verbose_name_plural = _("CP Forwarders")
        constraints = [
            models.UniqueConstraint(
                fields=["source_node", "target_node"], name="unique_forwarder_per_target"
            )
        ]
        ordering = ["target_node__hostname", "target_node__pk"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.name:
            return self.name
        label = str(self.target_node) if self.target_node else _("Unconfigured")
        if self.source_node:
            return f"{self.source_node} → {label}"
        return label

    def save(self, *args, **kwargs):
        sync_chargers = kwargs.pop("sync_chargers", True)
        self.forwarded_messages = self.sanitize_forwarded_messages(
            self.forwarded_messages
        )
        if self.source_node_id is None:
            local = Node.get_local()
            if local:
                self.source_node = local
        super().save(*args, **kwargs)
        if sync_chargers:
            self.sync_chargers()

    def delete(self, *args, **kwargs):
        was_enabled = self.enabled
        if was_enabled:
            self.enabled = False
        self.sync_chargers()
        super().delete(*args, **kwargs)
        forwarder.sync_forwarded_charge_points()

    def sync_chargers(self, *, apply_sessions: bool = True) -> None:
        """Apply the forwarder configuration to eligible charge points."""

        now = timezone.now()
        local_node, private_key, credential_error = load_local_node_credentials()
        eligible = self._eligible_chargers(local_node)
        status_parts: list[str] = []
        error_message: str | None = None
        session_active: bool | None = None

        if not self.target_node_id:
            error_message = _("Target node is not configured.")
            status_parts.append(_("Forwarder configuration is incomplete."))
            status_text = " ".join(str(part) for part in status_parts if str(part).strip()).strip()
            error_text = str(error_message) if error_message else ""
            self._update_fields(
                last_status=status_text,
                last_error=error_text,
                last_synced_at=now,
                is_running=False,
            )
            if apply_sessions:
                forwarder.sync_forwarded_charge_points(refresh_forwarders=False)
            return

        if not self.enabled:
            cleared = eligible.filter(forwarded_to=self.target_node).update(
                forwarded_to=None, forwarding_watermark=None
            )
            if cleared:
                status_parts.append(
                    _("Cleared forwarding for %(count)s charge point(s).")
                    % {"count": cleared}
                )
            else:
                status_parts.append(_("No forwarded charge points required clearing."))
            status_text = " ".join(str(part) for part in status_parts if str(part).strip()).strip()
            updates = {
                "last_status": status_text,
                "last_error": "",
                "last_synced_at": now,
            }
            if self.is_running:
                updates["is_running"] = False
            self._update_fields(**updates)
            if apply_sessions:
                forwarder.sync_forwarded_charge_points(refresh_forwarders=False)
            return

        conflicts = eligible.exclude(
            Q(forwarded_to__isnull=True) | Q(forwarded_to=self.target_node)
        ).count()
        chargers_to_update = list(
            eligible.filter(
                Q(forwarded_to__isnull=True) | Q(forwarded_to=self.target_node)
            )
        )

        updated_count = 0
        if chargers_to_update:
            from ocpp.models import Charger

            charger_pks = [charger.pk for charger in chargers_to_update if charger.pk]
            if charger_pks:
                Charger.objects.filter(pk__in=charger_pks).update(
                    forwarded_to=self.target_node
                )
                updated_count = len(charger_pks)
                status_parts.append(
                    _("Forwarding %(count)s charge point(s).")
                    % {"count": updated_count}
                )
        else:
            status_parts.append(_("No charge points were updated."))

        if conflicts:
            status_parts.append(
                _("Skipped %(count)s charge point(s) already forwarded elsewhere.")
                % {"count": conflicts}
            )

        sample = next((charger for charger in chargers_to_update if charger.charger_id), None)
        if sample and not attempt_forwarding_probe(self.target_node, sample.charger_id):
            status_parts.append(
                _("Probe failed for %(charger)s.") % {"charger": sample.charger_id}
            )

        if updated_count:
            if credential_error:
                error_message = credential_error
            elif local_node is None or private_key is None:
                error_message = _(
                    "Unable to sign forwarding metadata without a configured private key."
                )
            else:
                success, metadata_error = send_forwarding_metadata(
                    self.target_node,
                    chargers_to_update,
                    local_node,
                    private_key,
                    forwarded_messages=self.get_forwarded_messages(),
                )
                if success:
                    status_parts.append(
                        _("Forwarding metadata sent to %(node)s.")
                        % {"node": self.target_node}
                    )
                else:
                    error_message = metadata_error or _(
                        "Failed to send forwarding metadata to the remote node."
                    )

        elif credential_error and not error_message:
            error_message = credential_error

        if apply_sessions:
            forwarder.sync_forwarded_charge_points(refresh_forwarders=False)
            session_active = forwarder.is_target_active(self.target_node_id)
            targeted_exists = eligible.filter(forwarded_to=self.target_node).exists()
            if session_active:
                status_parts.append(_("Forwarding websocket is connected."))
            elif targeted_exists:
                status_parts.append(_("Forwarding websocket is not connected."))
                if not error_message:
                    error_message = _("Forwarding websocket inactive.")

        status_text = " ".join(str(part) for part in status_parts if str(part).strip()).strip()
        error_text = str(error_message) if error_message else ""
        updates = {
            "last_status": status_text,
            "last_error": error_text,
            "last_synced_at": now,
        }
        if apply_sessions:
            desired_running = bool(self.enabled and session_active)
            if self.is_running != desired_running:
                updates["is_running"] = desired_running
        self._update_fields(**updates)

    def mark_running(self, timestamp) -> None:
        updates = {}
        if not self.is_running:
            updates["is_running"] = True
        if timestamp and (self.last_forwarded_at is None or timestamp > self.last_forwarded_at):
            updates["last_forwarded_at"] = timestamp
        if updates:
            self._update_fields(**updates)

    def set_running_state(self, active: bool) -> None:
        if active and not self.is_running:
            self._update_fields(is_running=True)
        elif not active and self.is_running:
            self._update_fields(is_running=False)

    def _eligible_chargers(self, local_node: Node | None):
        from ocpp.models import Charger

        qs = Charger.objects.filter(export_transactions=True)
        if local_node and local_node.pk:
            qs = qs.filter(Q(node_origin=local_node) | Q(node_origin__isnull=True))
        return qs.select_related("forwarded_to")

    def _update_fields(self, **changes) -> None:
        if not changes or not self.pk:
            for key, value in changes.items():
                setattr(self, key, value)
            return
        type(self).objects.filter(pk=self.pk).update(**changes)
        for key, value in changes.items():
            setattr(self, key, value)

    @classmethod
    def available_forwarded_messages(cls) -> Sequence[str]:
        return tuple(OCPP_FORWARDING_MESSAGES)

    @classmethod
    def sanitize_forwarded_messages(cls, values: Iterable[str] | None) -> list[str]:
        if values is None:
            return list(OCPP_FORWARDING_MESSAGES)
        if isinstance(values, str):
            return list(OCPP_FORWARDING_MESSAGES)
        cleaned: list[str] = []
        order_map = {msg: idx for idx, msg in enumerate(OCPP_FORWARDING_MESSAGES)}
        for item in values:
            if item in order_map and item not in cleaned:
                cleaned.append(item)
        if cleaned:
            cleaned.sort(key=lambda msg: order_map[msg])
            return cleaned
        if isinstance(values, list) and not values:
            return []
        return list(OCPP_FORWARDING_MESSAGES)

    def get_forwarded_messages(self) -> list[str]:
        return self.sanitize_forwarded_messages(self.forwarded_messages)

    def forwards_action(self, action: str) -> bool:
        if not action:
            return False
        return action in self.get_forwarded_messages()

    @property
    def _forwarding_service(self):
        """Return the shared forwarding service used to manage sessions."""

        from ocpp.forwarder import forwarder

        return forwarder

    @property
    def _sessions(self):
        """Expose active sessions tracked by the forwarding service."""

        return self._forwarding_service._sessions

    def get_session(self, charger_pk: int):
        """Return the active forwarding session for ``charger_pk`` when present."""

        return self._forwarding_service.get_session(charger_pk)

    def remove_session(self, charger_pk: int) -> None:
        """Remove the forwarding session for ``charger_pk`` if it exists."""

        self._forwarding_service.remove_session(charger_pk)
