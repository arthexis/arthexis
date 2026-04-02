from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin
from apps.netmesh.models import (
    MeshMembership,
    NodeRelayConfig,
    NodeEndpoint,
    NodeKeyMaterial,
    PeerPolicy,
    RelayRegion,
    ServiceAdvertisement,
)


class DirectConnectivityFilter(admin.SimpleListFilter):
    title = "direct connectivity"
    parameter_name = "direct_connectivity"

    def lookups(self, request, model_admin):
        return (
            ("relay_only", "Relay-only"),
            ("failing_direct", "Failing direct"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "relay_only":
            return queryset.filter(relay_required=True)
        if value == "failing_direct":
            return queryset.filter(last_successful_direct_at__isnull=True)
        return queryset


@admin.register(MeshMembership)
class MeshMembershipAdmin(EntityModelAdmin):
    list_display = ("node", "tenant", "site", "is_enabled")
    list_filter = ("is_enabled", "site")
    search_fields = ("node__hostname", "tenant", "site__domain")


@admin.register(NodeKeyMaterial)
class NodeKeyMaterialAdmin(EntityModelAdmin):
    list_display = ("node", "created_at", "rotated_at", "revoked", "revoked_at")
    list_filter = ("revoked",)
    search_fields = ("node__hostname", "public_key")


@admin.register(PeerPolicy)
class PeerPolicyAdmin(EntityModelAdmin):
    list_display = (
        "tenant",
        "site",
        "source_node",
        "source_group",
        "source_station",
        "destination_node",
        "destination_group",
        "destination_station",
        "policy_summary",
    )
    list_filter = ("site", "source_group", "destination_group")
    search_fields = (
        "tenant",
        "site__domain",
        "source_node__hostname",
        "destination_node__hostname",
        "source_group__name",
        "destination_group__name",
        "source_station__charger_id",
        "destination_station__charger_id",
    )

    @admin.display(description="Policy summary")
    def policy_summary(self, obj):
        allow = ", ".join(obj.normalized_allowed_services()) or "none"
        deny = ", ".join(obj.normalized_denied_services()) or "none"
        return f"allow: {allow} | deny: {deny}"


@admin.register(NodeEndpoint)
class NodeEndpointAdmin(EntityModelAdmin):
    list_display = (
        "node",
        "endpoint",
        "endpoint_priority",
        "nat_type",
        "relay_required",
        "last_successful_direct_at",
        "last_seen",
    )
    list_filter = ("nat_type", "relay_required", DirectConnectivityFilter)
    search_fields = ("node__hostname", "endpoint")


@admin.register(RelayRegion)
class RelayRegionAdmin(EntityModelAdmin):
    list_display = ("code", "name", "relay_endpoint", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "relay_endpoint")


@admin.register(NodeRelayConfig)
class NodeRelayConfigAdmin(EntityModelAdmin):
    list_display = ("node", "region", "priority", "is_enabled")
    list_filter = ("is_enabled", "region")
    search_fields = ("node__hostname", "region__code", "relay_endpoint")


@admin.register(ServiceAdvertisement)
class ServiceAdvertisementAdmin(EntityModelAdmin):
    list_display = ("node", "service_name", "port", "protocol")
    list_filter = ("protocol",)
    search_fields = ("node__hostname", "service_name")
