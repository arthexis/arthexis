from django.contrib import admin

from apps.nodes.models import NodeEnrollment, NodeEnrollmentEvent


@admin.register(NodeEnrollment)
class NodeEnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        "node",
        "site",
        "status",
        "token_hint",
        "expires_at",
        "used_at",
        "last_authenticated_at",
        "last_auth_error_code",
        "revoked_at",
        "created_at",
    )
    list_filter = ("status", "scope", "site")
    search_fields = ("node__hostname", "node__mac_address", "token_hint", "scope")
    readonly_fields = (
        "node",
        "site",
        "issued_by",
        "scope",
        "token_hint",
        "status",
        "expires_at",
        "used_at",
        "last_authenticated_at",
        "last_auth_error_code",
        "revoked_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(NodeEnrollmentEvent)
class NodeEnrollmentEventAdmin(admin.ModelAdmin):
    list_display = (
        "node",
        "action",
        "from_state",
        "to_state",
        "actor",
        "created_at",
    )
    list_filter = ("action", "from_state", "to_state")
    search_fields = ("node__hostname", "node__mac_address", "details")
    readonly_fields = (
        "node",
        "enrollment",
        "action",
        "from_state",
        "to_state",
        "actor",
        "details",
        "created_at",
    )

    def has_add_permission(self, request):
        return False
