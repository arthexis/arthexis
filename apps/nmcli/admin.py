from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from .models import NetworkConnection
from .services import NMCLIScanError, scan_nmcli_connections


@admin.register(NetworkConnection)
class NetworkConnectionAdmin(DjangoObjectActions, admin.ModelAdmin):
    actions = ["run_nmcli_scan"]
    changelist_actions = ["run_nmcli_scan"]
    list_display = (
        "connection_id",
        "connection_type",
        "interface_name",
        "autoconnect",
        "last_nmcli_check",
    )
    list_filter = ("connection_type", "autoconnect", "metered")
    search_fields = (
        "connection_id",
        "uuid",
        "interface_name",
        "wireless_ssid",
        "mac_address",
    )
    readonly_fields = ("last_nmcli_check", "last_modified_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "connection_id",
                    "uuid",
                    "connection_type",
                    "interface_name",
                    "autoconnect",
                    "priority",
                    "metered",
                    "mac_address",
                )
            },
        ),
        (
            "IPv4",
            {"fields": ("ip4_method", "ip4_address", "ip4_gateway", "ip4_dns")},
        ),
        (
            "IPv6",
            {"fields": ("ip6_method", "ip6_address", "ip6_gateway", "ip6_dns")},
        ),
        (
            "DHCP",
            {"fields": ("dhcp_client_id", "dhcp_hostname")},
        ),
        (
            "Wireless",
            {
                "fields": (
                    "wireless_ssid",
                    "wireless_mode",
                    "wireless_band",
                    "wireless_channel",
                )
            },
        ),
        (
            "Security",
            {"fields": ("security_type", "password")},
        ),
        (
            "Timestamps",
            {"fields": ("last_nmcli_check", "last_modified_at")},
        ),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "run-scan/",
                self.admin_site.admin_view(self.run_nmcli_scan_view),
                name="nmcli_networkconnection_run_nmcli_scan",
            ),
        ]
        return custom + urls

    def _scan_url(self) -> str:
        return reverse("admin:nmcli_networkconnection_run_nmcli_scan")

    def run_nmcli_scan(self, request, queryset=None):
        return HttpResponseRedirect(self._scan_url())

    run_nmcli_scan.label = _("Run NMCLI Scan")
    run_nmcli_scan.short_description = _("Run NMCLI Scan")
    run_nmcli_scan.requires_queryset = False

    def _sync_connections(self, request):
        scanned, errors = scan_nmcli_connections()
        now = timezone.now()
        created = 0
        updated = 0

        for entry in scanned:
            if not entry.get("connection_id") and not entry.get("uuid"):
                errors.append("Missing connection identifiers; skipping entry.")
                continue

            defaults = {
                "connection_id": entry.get("connection_id") or "",
                "uuid": entry.get("uuid") or None,
                "connection_type": entry.get("connection_type", ""),
                "interface_name": entry.get("interface_name", ""),
                "autoconnect": entry.get("autoconnect", False),
                "priority": entry.get("priority"),
                "metered": entry.get("metered", ""),
                "ip4_address": entry.get("ip4_address", ""),
                "ip4_method": entry.get("ip4_method", ""),
                "ip4_gateway": entry.get("ip4_gateway", ""),
                "ip4_dns": entry.get("ip4_dns", ""),
                "ip6_address": entry.get("ip6_address", ""),
                "ip6_method": entry.get("ip6_method", ""),
                "ip6_gateway": entry.get("ip6_gateway", ""),
                "ip6_dns": entry.get("ip6_dns", ""),
                "dhcp_client_id": entry.get("dhcp_client_id", ""),
                "dhcp_hostname": entry.get("dhcp_hostname", ""),
                "wireless_ssid": entry.get("wireless_ssid", ""),
                "wireless_mode": entry.get("wireless_mode", ""),
                "wireless_band": entry.get("wireless_band", ""),
                "wireless_channel": entry.get("wireless_channel", ""),
                "security_type": entry.get("security_type", ""),
                "password": entry.get("password", ""),
                "mac_address": entry.get("mac_address", ""),
                "last_nmcli_check": now,
                "last_modified_at": entry.get("last_modified_at"),
            }

            lookup = (
                {"uuid": defaults["uuid"]}
                if defaults["uuid"]
                else {"connection_id": defaults["connection_id"]}
            )
            obj, created_flag = NetworkConnection.objects.update_or_create(
                defaults=defaults,
                **lookup,
            )
            if created_flag:
                created += 1
            else:
                updated += 1

        return {"created": created, "updated": updated, "errors": errors, "count": len(scanned)}

    def run_nmcli_scan_view(self, request):
        opts = self.model._meta
        changelist_url = reverse("admin:nmcli_networkconnection_changelist")
        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "title": _("Run NMCLI Scan"),
            "changelist_url": changelist_url,
            "scan_url": self._scan_url(),
            "result": None,
        }

        if request.method == "POST":
            try:
                result = self._sync_connections(request)
            except NMCLIScanError as exc:
                self.message_user(request, str(exc), messages.ERROR)
            else:
                context["result"] = result
                if result["created"] or result["updated"]:
                    self.message_user(
                        request,
                        _(
                            "NMCLI scan completed. %(created)d created, %(updated)d updated."
                        )
                        % {"created": result["created"], "updated": result["updated"]},
                        messages.SUCCESS,
                    )
                if result.get("errors"):
                    for error in result["errors"]:
                        self.message_user(request, error, messages.WARNING)

        return TemplateResponse(
            request, "admin/nmcli/networkconnection/run_scan.html", context
        )
