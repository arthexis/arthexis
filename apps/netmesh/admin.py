from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from apps.locals.user_data import EntityModelAdmin
from apps.netmesh.models import (
    MeshMembership,
    NetmeshAgentStatus,
    NodeEndpoint,
    NodeKeyMaterial,
    NodeRelayConfig,
    PeerPolicy,
    RelayRegion,
    ServiceAdvertisement,
)
from apps.nodes.services.enrollment import issue_enrollment_token, revoke_enrollment


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


class TenantFilter(admin.SimpleListFilter):
    title = "tenant"
    parameter_name = "tenant"

    def lookups(self, request, model_admin):
        tenants = (
            model_admin.get_queryset(request)
            .exclude(tenant="")
            .values_list("tenant", flat=True)
            .distinct()
            .order_by("tenant")
        )
        return [(tenant, tenant) for tenant in tenants]

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            return queryset.filter(tenant=value)
        return queryset


@admin.register(MeshMembership)
class MeshMembershipAdmin(EntityModelAdmin):
    actions = ("quarantine_segment", "revoke_selected_nodes")
    list_display = ("node", "tenant", "site", "overlay_ipv4", "is_enabled")
    list_filter = ("is_enabled", "site", TenantFilter)
    search_fields = ("node__hostname", "tenant", "site__domain", "overlay_lease__overlay_ipv4")
    list_select_related = ("node", "site", "overlay_lease")
    readonly_fields = ("overlay_ipv4",)

    @admin.display(description="Overlay IPv4")
    def overlay_ipv4(self, obj):
        return getattr(getattr(obj, "overlay_lease", None), "overlay_ipv4", "—")

    @admin.action(description="Quarantine segment")
    def quarantine_segment(self, request, queryset):
        return self._confirm_membership_action(
            request,
            queryset,
            action_name="quarantine_segment",
            title=_("Quarantine selected mesh segments"),
            submit_label=_("Confirm quarantine"),
            prompt=_("Disable selected memberships for incident containment."),
        )

    @admin.action(description="Revoke selected nodes")
    def revoke_selected_nodes(self, request, queryset):
        return self._confirm_membership_action(
            request,
            queryset,
            action_name="revoke_selected_nodes",
            title=_("Revoke selected mesh nodes"),
            submit_label=_("Confirm revoke"),
            prompt=_("Revoke enrollment and disable memberships for selected nodes."),
        )

    def _confirm_membership_action(self, request, queryset, *, action_name, title, submit_label, prompt):
        select_across = request.POST.get("select_across") == "1"
        if select_across:
            membership_queryset = queryset
        else:
            selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
            if selected_ids:
                membership_queryset = self.get_queryset(request).filter(pk__in=selected_ids)
            else:
                membership_queryset = queryset

        memberships = list(membership_queryset.select_related("node", "site"))
        if not memberships:
            self.message_user(request, _("No memberships selected."), messages.INFO)
            return None

        if request.POST.get("apply") == "1":
            with transaction.atomic():
                if action_name == "quarantine_segment":
                    changed = 0
                    for membership in memberships:
                        if membership.is_enabled:
                            membership.is_enabled = False
                            membership.save(update_fields=["is_enabled"])
                            changed += 1
                    self.message_user(
                        request,
                        ngettext(
                            "Quarantined %(count)d membership.",
                            "Quarantined %(count)d memberships.",
                            changed,
                        )
                        % {"count": changed},
                        messages.WARNING,
                    )
                else:
                    nodes = {}
                    for membership in memberships:
                        nodes[membership.node_id] = membership.node
                        if membership.is_enabled:
                            membership.is_enabled = False
                            membership.save(update_fields=["is_enabled"])
                    for node in nodes.values():
                        revoke_enrollment(node=node, actor=request.user, reason="netmesh incident response")

                    changed = len(nodes)
                    self.message_user(
                        request,
                        ngettext(
                            "Revoked %(count)d node.",
                            "Revoked %(count)d nodes.",
                            changed,
                        )
                        % {"count": changed},
                        messages.WARNING,
                    )
            return HttpResponseRedirect(request.get_full_path())

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": title,
            "memberships": memberships,
            "selected_ids": [str(m.pk) for m in memberships],
            "action_name": action_name,
            "select_across": "1" if select_across else "0",
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "submit_label": submit_label,
            "prompt": prompt,
        }
        return TemplateResponse(request, "admin/netmesh/meshmembership/confirm_action.html", context)


@admin.register(NodeKeyMaterial)
class NodeKeyMaterialAdmin(EntityModelAdmin):
    list_display = ("node", "key_type", "key_version", "key_state", "created_at", "rotated_at", "revoked_at")
    list_filter = ("key_type", "key_state")
    search_fields = ("node__hostname", "public_key")


@admin.register(PeerPolicy)
class PeerPolicyAdmin(EntityModelAdmin):
    change_list_template = "admin/netmesh/peerpolicy/change_list.html"
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
    list_filter = ("site", "source_group", "destination_group", TenantFilter)
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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "matrix/",
                self.admin_site.admin_view(self.policy_matrix_view),
                name="netmesh_peerpolicy_matrix",
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["policy_matrix_url"] = reverse("admin:netmesh_peerpolicy_matrix")
        return super().changelist_view(request, extra_context=extra_context)

    def policy_matrix_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        policies = list(
            self.get_queryset(request)
            .select_related("site", "source_node", "source_group", "destination_node", "destination_group")
            .order_by("tenant", "site__domain", "id")
        )
        rows = []
        for policy in policies:
            rows.append(
                {
                    "tenant": policy.tenant or "default",
                    "site": policy.site.domain if policy.site_id and policy.site else "global",
                    "source": policy.source_node or policy.source_group or policy.source_station or ", ".join(policy.normalized_source_tags()) or "any",
                    "destination": policy.destination_node
                    or policy.destination_group
                    or policy.destination_station
                    or ", ".join(policy.normalized_destination_tags())
                    or "any",
                    "allow": ", ".join(policy.normalized_allowed_services()) or "none",
                    "deny": ", ".join(policy.normalized_denied_services()) or "none",
                }
            )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Policy matrix"),
            "rows": rows,
            "changelist_url": reverse("admin:netmesh_peerpolicy_changelist"),
        }
        return TemplateResponse(request, "admin/netmesh/peerpolicy/matrix.html", context)


@admin.register(NodeEndpoint)
class NodeEndpointAdmin(EntityModelAdmin):
    change_list_template = "admin/netmesh/nodeendpoint/change_list.html"
    list_display = (
        "node",
        "endpoint",
        "endpoint_priority",
        "nat_type",
        "relay_required",
        "health_status",
        "last_successful_direct_at",
        "last_seen",
    )
    list_filter = ("nat_type", "relay_required", DirectConnectivityFilter)
    search_fields = ("node__hostname", "endpoint")

    @admin.display(description="Health")
    def health_status(self, obj):
        if obj.last_successful_direct_at:
            age = timezone.now() - obj.last_successful_direct_at
            if age <= timedelta(hours=1):
                return format_html('<span style="color:#198754;font-weight:600;">{}</span>', "healthy")
            if age <= timedelta(hours=24):
                return format_html('<span style="color:#fd7e14;font-weight:600;">{}</span>', "stale")
        return format_html('<span style="color:#dc3545;font-weight:600;">{}</span>', "degraded")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "health/",
                self.admin_site.admin_view(self.endpoint_health_view),
                name="netmesh_nodeendpoint_health",
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["endpoint_health_url"] = reverse("admin:netmesh_nodeendpoint_health")
        return super().changelist_view(request, extra_context=extra_context)

    def endpoint_health_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        endpoints = []
        for endpoint in self.get_queryset(request).select_related("node").order_by("node__hostname", "endpoint_priority"):
            if endpoint.last_successful_direct_at:
                age = timezone.now() - endpoint.last_successful_direct_at
                if age <= timedelta(hours=1):
                    health = "healthy"
                elif age <= timedelta(hours=24):
                    health = "stale"
                else:
                    health = "degraded"
            else:
                health = "degraded"
            endpoints.append({"endpoint": endpoint, "health": health})
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Endpoint health"),
            "endpoints": endpoints,
            "changelist_url": reverse("admin:netmesh_nodeendpoint_changelist"),
        }
        return TemplateResponse(request, "admin/netmesh/nodeendpoint/health.html", context)


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


@admin.register(NetmeshAgentStatus)
class NetmeshAgentStatusAdmin(EntityModelAdmin):
    list_display = (
        "singleton",
        "is_running",
        "lifecycle_state",
        "last_poll_at",
        "peers_synced",
        "session_count",
        "relay_count",
    )
    readonly_fields = (
        "singleton",
        "is_running",
        "lifecycle_state",
        "last_poll_at",
        "last_sync_at",
        "peers_synced",
        "session_count",
        "relay_count",
        "last_error",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
