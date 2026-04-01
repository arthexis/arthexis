from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin
from apps.netmesh.models import (
    MeshMembership,
    NodeEndpoint,
    NodeKeyMaterial,
    PeerPolicy,
    ServiceAdvertisement,
)


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
        "destination_node",
        "destination_group",
    )
    list_filter = ("site", "source_group", "destination_group")
    search_fields = (
        "tenant",
        "site__domain",
        "source_node__hostname",
        "destination_node__hostname",
        "source_group__name",
        "destination_group__name",
    )


@admin.register(NodeEndpoint)
class NodeEndpointAdmin(EntityModelAdmin):
    list_display = ("node", "endpoint", "nat_type", "last_seen")
    list_filter = ("nat_type",)
    search_fields = ("node__hostname", "endpoint")


@admin.register(ServiceAdvertisement)
class ServiceAdvertisementAdmin(EntityModelAdmin):
    list_display = ("node", "service_name", "port", "protocol")
    list_filter = ("protocol",)
    search_fields = ("node__hostname", "service_name")
