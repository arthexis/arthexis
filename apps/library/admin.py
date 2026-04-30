from django.contrib import admin

from .models import (
    KindleLibraryTransfer,
    OwnerLibraryHolding,
    PublicLibraryEntry,
    RegisteredKindle,
)


class OwnerLibraryHoldingInline(admin.TabularInline):
    model = OwnerLibraryHolding
    extra = 0
    raw_id_fields = ("owner",)
    fields = (
        "owner",
        "status",
        "source_account_label",
        "local_version",
        "remote_version",
        "backup_version",
        "reconciliation_state",
        "reconciled_at",
    )
    readonly_fields = ("reconciled_at",)
    show_change_link = True


class KindleLibraryTransferInline(admin.TabularInline):
    model = KindleLibraryTransfer
    extra = 0
    raw_id_fields = ("registered_kindle",)
    fields = (
        "registered_kindle",
        "operation",
        "direction",
        "status",
        "reconciliation_state",
        "started_at",
        "finished_at",
    )
    readonly_fields = ("started_at", "finished_at")
    show_change_link = True


@admin.register(RegisteredKindle)
class RegisteredKindleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "serial_number",
        "owner",
        "status",
        "software_version",
        "last_seen_at",
    )
    list_filter = ("status", "registered_at", "last_seen_at")
    search_fields = (
        "name",
        "serial_number",
        "kindle_identifier",
        "device_email",
        "owner__username",
    )
    raw_id_fields = ("owner",)
    readonly_fields = ("registered_at",)


@admin.register(PublicLibraryEntry)
class PublicLibraryEntryAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "creators",
        "source",
        "external_id",
        "asin",
        "content_format",
        "remote_modified_at",
    )
    list_filter = ("source", "content_format", "language", "remote_modified_at")
    search_fields = (
        "title",
        "creators",
        "external_id",
        "asin",
        "isbn",
        "content_checksum_sha256",
    )
    readonly_fields = ("created_at", "updated_at")
    inlines = (OwnerLibraryHoldingInline,)


@admin.register(OwnerLibraryHolding)
class OwnerLibraryHoldingAdmin(admin.ModelAdmin):
    list_display = (
        "entry",
        "owner",
        "status",
        "local_version",
        "remote_version",
        "backup_version",
        "reconciliation_state",
        "reconciled_at",
    )
    list_filter = ("status", "reconciliation_state", "acquired_at", "reconciled_at")
    search_fields = (
        "entry__title",
        "entry__creators",
        "owner__username",
        "local_path",
        "backup_path",
        "local_checksum_sha256",
        "remote_checksum_sha256",
        "backup_checksum_sha256",
    )
    raw_id_fields = ("owner", "entry")
    readonly_fields = ("created_at", "updated_at", "reconciled_at")
    inlines = (KindleLibraryTransferInline,)


@admin.register(KindleLibraryTransfer)
class KindleLibraryTransferAdmin(admin.ModelAdmin):
    list_display = (
        "holding",
        "registered_kindle",
        "operation",
        "direction",
        "status",
        "reconciliation_state",
        "started_at",
        "finished_at",
    )
    list_filter = (
        "operation",
        "direction",
        "status",
        "reconciliation_state",
        "started_at",
        "finished_at",
    )
    search_fields = (
        "holding__entry__title",
        "holding__owner__username",
        "registered_kindle__name",
        "registered_kindle__serial_number",
        "error_message",
    )
    raw_id_fields = ("registered_kindle", "holding")
    readonly_fields = ("duration",)
