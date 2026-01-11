from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .forms import SSHAccountAdminForm
from .models import SSHAccount


@admin.register(SSHAccount)
class SSHAccountAdmin(admin.ModelAdmin):
    form = SSHAccountAdminForm
    list_display = (
        "username",
        "node",
        "authentication_method",
        "updated_at",
    )
    list_filter = ("node",)
    search_fields = (
        "username",
        "node__hostname",
        "node__network_hostname",
        "node__mac_address",
    )
    readonly_fields = ("created_at", "updated_at", "private_key_metadata", "public_key_metadata")
    fields = (
        "node",
        "username",
        "password",
        "private_key_media",
        "private_key_upload",
        "private_key_metadata",
        "public_key_media",
        "public_key_upload",
        "public_key_metadata",
        "created_at",
        "updated_at",
    )

    @admin.display(description=_("Authentication"))
    def authentication_method(self, obj: SSHAccount) -> str:
        if obj.private_key_media_id or obj.public_key_media_id:
            return _("SSH key")
        if (obj.password or "").strip():
            return _("Password")
        return _("Not set")

    @admin.display(description=_("Private key metadata"))
    def private_key_metadata(self, obj: SSHAccount) -> str:
        media = getattr(obj, "private_key_media", None)
        if not media:
            return _("No private key uploaded")
        return _("%(name)s (%(type)s, %(size)s bytes)") % {
            "name": media.original_name or media.file.name,
            "type": media.content_type or _("unknown"),
            "size": media.size,
        }

    @admin.display(description=_("Public key metadata"))
    def public_key_metadata(self, obj: SSHAccount) -> str:
        media = getattr(obj, "public_key_media", None)
        if not media:
            return _("No public key uploaded")
        return _("%(name)s (%(type)s, %(size)s bytes)") % {
            "name": media.original_name or media.file.name,
            "type": media.content_type or _("unknown"),
            "size": media.size,
        }
