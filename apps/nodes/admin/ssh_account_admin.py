from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from ..models import SSHAccount


@admin.register(SSHAccount)
class SSHAccountAdmin(admin.ModelAdmin):
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
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description=_("Authentication"))
    def authentication_method(self, obj: SSHAccount) -> str:
        if obj.private_key or obj.public_key:
            return _("SSH key")
        if (obj.password or "").strip():
            return _("Password")
        return _("Not set")
