from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from .models import BluetoothAdapter, BluetoothDevice
from .services import (
    BluetoothCommandError,
    BluetoothParseError,
    discover_and_sync_devices,
)


DEFAULT_DISCOVERY_TIMEOUT_S = 60


@admin.register(BluetoothAdapter)
class BluetoothAdapterAdmin(admin.ModelAdmin):
    """Admin configuration for Bluetooth adapters."""

    list_display = (
        "name",
        "address",
        "alias",
        "powered",
        "discoverable",
        "pairable",
        "last_checked_at",
    )
    search_fields = ("name", "address", "alias")
    readonly_fields = ("last_checked_at",)


@admin.register(BluetoothDevice)
class BluetoothDeviceAdmin(DjangoObjectActions, admin.ModelAdmin):
    """Admin configuration for discovered Bluetooth devices."""

    actions = ["mark_registered", "mark_unregistered", "run_discovery"]
    changelist_actions = ["run_discovery"]
    list_display = (
        "address",
        "name",
        "alias",
        "is_registered",
        "paired",
        "trusted",
        "connected",
        "last_seen_at",
    )
    list_filter = ("is_registered", "paired", "trusted", "connected", "adapter")
    search_fields = ("address", "name", "alias")
    readonly_fields = ("first_seen_at", "last_seen_at", "registered_at")

    def mark_registered(self, request, queryset):
        """Bulk mark selected devices as registered."""

        updated = queryset.update(
            is_registered=True, registered_at=timezone.now(), registered_by=request.user
        )
        self.message_user(
            request,
            _("Marked %(count)d device(s) as registered.") % {"count": updated},
            messages.SUCCESS,
        )

    def mark_unregistered(self, request, queryset):
        """Bulk mark selected devices as unregistered."""

        updated = queryset.update(
            is_registered=False, registered_at=None, registered_by=None
        )
        self.message_user(
            request,
            _("Marked %(count)d device(s) as unregistered.") % {"count": updated},
            messages.SUCCESS,
        )

    @admin.action(description=_("Discover"))
    def run_discovery(self, request, queryset=None):
        """Redirect to discovery form page."""

        return HttpResponseRedirect(self._discover_url())

    run_discovery.label = _("Discover")
    run_discovery.requires_queryset = False
    run_discovery.is_discover_action = True

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "discover/",
                self.admin_site.admin_view(self.discover_view),
                name="bluetooth_bluetoothdevice_discover",
            ),
        ]
        return custom + urls

    def _discover_url(self) -> str:
        return reverse("admin:bluetooth_bluetoothdevice_discover")

    def discover_view(self, request):
        """Run Bluetooth discovery from the admin page."""

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Discover"),
            "scan_url": self._discover_url(),
            "changelist_url": reverse("admin:bluetooth_bluetoothdevice_changelist"),
            "default_timeout": DEFAULT_DISCOVERY_TIMEOUT_S,
            "result": None,
        }
        if request.method == "POST":
            try:
                timeout_s = int(request.POST.get("timeout_s", str(DEFAULT_DISCOVERY_TIMEOUT_S)))
                timeout_s = max(0, min(timeout_s, DEFAULT_DISCOVERY_TIMEOUT_S))
                result = discover_and_sync_devices(timeout_s=timeout_s)
            except (ValueError, BluetoothCommandError, BluetoothParseError) as exc:
                context["error"] = str(exc)
                self.message_user(request, str(exc), messages.ERROR)
            else:
                context["result"] = result
                self.message_user(
                    request,
                    _(
                        "Bluetooth discovery completed. %(created)d created, %(updated)d updated."
                    )
                    % {"created": result["created"], "updated": result["updated"]},
                    messages.SUCCESS,
                )
        return TemplateResponse(
            request, "admin/bluetooth/bluetoothdevice/run_discovery.html", context
        )
