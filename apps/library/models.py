from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class RegisteredKindle(Entity):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        RETIRED = "retired", _("Retired")
        LOST = "lost", _("Lost")

    name = models.CharField(max_length=120)
    serial_number = models.CharField(max_length=80, unique=True, db_index=True)
    kindle_identifier = models.CharField(max_length=120, blank=True, default="")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registered_kindles",
    )
    device_email = models.EmailField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    software_version = models.CharField(max_length=80, blank=True, default="")
    registered_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("name", "serial_number")
        verbose_name = _("Registered Kindle")
        verbose_name_plural = _("Registered Kindles")

    def __str__(self) -> str:
        return f"{self.name} ({self.serial_number})"


class PublicLibraryEntry(Entity):
    class Source(models.TextChoices):
        KINDLE_STORE = "kindle_store", _("Kindle Store")
        PUBLIC_DOMAIN = "public_domain", _("Public domain")
        OWNER_UPLOAD = "owner_upload", _("Owner upload")
        OTHER = "other", _("Other")

    class Format(models.TextChoices):
        EPUB = "epub", _("EPUB")
        KFX = "kfx", _("KFX")
        MOBI = "mobi", _("MOBI")
        PDF = "pdf", _("PDF")
        AZW = "azw", _("AZW")
        UNKNOWN = "unknown", _("Unknown")

    source = models.CharField(
        max_length=30, choices=Source.choices, default=Source.KINDLE_STORE
    )
    external_id = models.CharField(
        max_length=180, blank=True, default="", db_index=True
    )
    title = models.CharField(max_length=255)
    creators = models.CharField(max_length=512, blank=True, default="")
    asin = models.CharField(max_length=40, blank=True, default="", db_index=True)
    isbn = models.CharField(max_length=40, blank=True, default="", db_index=True)
    publisher = models.CharField(max_length=255, blank=True, default="")
    language = models.CharField(max_length=16, blank=True, default="")
    publication_date = models.DateField(null=True, blank=True)
    content_format = models.CharField(
        max_length=20, choices=Format.choices, default=Format.UNKNOWN
    )
    remote_version = models.CharField(max_length=120, blank=True, default="")
    remote_etag = models.CharField(max_length=180, blank=True, default="")
    remote_modified_at = models.DateTimeField(null=True, blank=True)
    content_checksum_sha256 = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("title", "creators")
        verbose_name = _("Public Library Entry")
        verbose_name_plural = _("Public Library Entries")
        constraints = [
            models.UniqueConstraint(
                fields=("source", "external_id"),
                condition=~Q(external_id=""),
                name="library_entry_unique_source_external_id",
            )
        ]

    def __str__(self) -> str:
        if self.creators:
            return f"{self.title} - {self.creators}"
        return self.title


class OwnerLibraryHolding(Entity):
    class Status(models.TextChoices):
        OWNED = "owned", _("Owned")
        BORROWED = "borrowed", _("Borrowed")
        ARCHIVED = "archived", _("Archived")
        REMOVED = "removed", _("Removed")

    class ReconciliationState(models.TextChoices):
        UNKNOWN = "unknown", _("Unknown")
        MATCHED = "matched", _("Matched")
        LOCAL_NEWER = "local_newer", _("Local newer")
        REMOTE_NEWER = "remote_newer", _("Remote newer")
        CONFLICT = "conflict", _("Conflict")
        MISSING_LOCAL = "missing_local", _("Missing local")
        MISSING_REMOTE = "missing_remote", _("Missing remote")

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="library_holdings",
    )
    entry = models.ForeignKey(
        PublicLibraryEntry, on_delete=models.CASCADE, related_name="owner_holdings"
    )
    source_account_label = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OWNED
    )
    acquired_at = models.DateTimeField(null=True, blank=True)

    local_path = models.CharField(max_length=512, blank=True, default="")
    local_version = models.CharField(max_length=120, blank=True, default="")
    local_modified_at = models.DateTimeField(null=True, blank=True)
    local_size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    local_checksum_sha256 = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )

    remote_version = models.CharField(max_length=120, blank=True, default="")
    remote_modified_at = models.DateTimeField(null=True, blank=True)
    remote_size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    remote_checksum_sha256 = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )

    backup_path = models.CharField(max_length=512, blank=True, default="")
    backup_version = models.CharField(max_length=120, blank=True, default="")
    backup_modified_at = models.DateTimeField(null=True, blank=True)
    backup_size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    backup_checksum_sha256 = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )

    reconciliation_state = models.CharField(
        max_length=30,
        choices=ReconciliationState.choices,
        default=ReconciliationState.UNKNOWN,
    )
    reconciled_at = models.DateTimeField(null=True, blank=True)
    reconciliation_notes = models.TextField(blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("owner", "entry__title")
        verbose_name = _("Owner Library Holding")
        verbose_name_plural = _("Owner Library Holdings")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "entry", "source_account_label"),
                name="library_holding_unique_owner_entry_account",
            )
        ]

    def __str__(self) -> str:
        return f"{self.owner} - {self.entry}"

    def local_is_newer(self) -> bool:
        if self.local_modified_at and self.remote_modified_at:
            return self.local_modified_at > self.remote_modified_at
        return bool(self.local_modified_at and not self.remote_modified_at)

    def remote_is_newer(self) -> bool:
        if self.local_modified_at and self.remote_modified_at:
            return self.remote_modified_at > self.local_modified_at
        return bool(self.remote_modified_at and not self.local_modified_at)

    def refresh_reconciliation_state(self, *, save: bool = False) -> str:
        if (
            self.local_checksum_sha256
            and self.local_checksum_sha256 == self.remote_checksum_sha256
        ):
            state = self.ReconciliationState.MATCHED
        elif not self.local_path and self.remote_version:
            state = self.ReconciliationState.MISSING_LOCAL
        elif self.local_path and not self.remote_version:
            state = self.ReconciliationState.MISSING_REMOTE
        # Phase 1 uses timestamp precedence once both sides appear present: if
        # they diverge, the newer mtime wins and equal/unknown mtimes conflict.
        elif self.local_is_newer():
            state = self.ReconciliationState.LOCAL_NEWER
        elif self.remote_is_newer():
            state = self.ReconciliationState.REMOTE_NEWER
        elif self.local_checksum_sha256 and self.remote_checksum_sha256:
            state = self.ReconciliationState.CONFLICT
        else:
            state = self.ReconciliationState.UNKNOWN

        self.reconciliation_state = state
        self.reconciled_at = timezone.now()
        if save:
            self.save(
                update_fields=("reconciliation_state", "reconciled_at", "updated_at")
            )
        return state


class KindleLibraryTransfer(Entity):
    class Operation(models.TextChoices):
        SYNC = "sync", _("Sync")
        COPY = "copy", _("Copy")
        BACKUP = "backup", _("Backup")
        RECONCILE = "reconcile", _("Reconcile")

    class Direction(models.TextChoices):
        LOCAL_TO_REMOTE = "local_to_remote", _("Local to remote")
        REMOTE_TO_LOCAL = "remote_to_local", _("Remote to local")
        DEVICE_TO_LIBRARY = "device_to_library", _("Device to library")
        LIBRARY_TO_DEVICE = "library_to_device", _("Library to device")
        NONE = "none", _("None")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")
        CONFLICT = "conflict", _("Conflict")

    registered_kindle = models.ForeignKey(
        RegisteredKindle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="library_transfers",
    )
    holding = models.ForeignKey(
        OwnerLibraryHolding, on_delete=models.CASCADE, related_name="transfers"
    )
    operation = models.CharField(
        max_length=20, choices=Operation.choices, default=Operation.SYNC
    )
    direction = models.CharField(
        max_length=30, choices=Direction.choices, default=Direction.NONE
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    local_version = models.CharField(max_length=120, blank=True, default="")
    remote_version = models.CharField(max_length=120, blank=True, default="")
    local_modified_at = models.DateTimeField(null=True, blank=True)
    remote_modified_at = models.DateTimeField(null=True, blank=True)
    local_checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    remote_checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    bytes_copied = models.PositiveBigIntegerField(null=True, blank=True)
    reconciliation_state = models.CharField(
        max_length=30,
        choices=OwnerLibraryHolding.ReconciliationState.choices,
        default=OwnerLibraryHolding.ReconciliationState.UNKNOWN,
    )
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ("-started_at", "-id")
        verbose_name = _("Kindle Library Transfer")
        verbose_name_plural = _("Kindle Library Transfers")

    def __str__(self) -> str:
        return f"{self.get_operation_display()} {self.get_status_display()} for {self.holding}"

    @property
    def duration(self):
        if not self.finished_at:
            return None
        return self.finished_at - self.started_at
