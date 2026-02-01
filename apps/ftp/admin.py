import socket

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.core.admin import OwnableAdminMixin

from .models import FTPFolder, FTPServer


@admin.register(FTPServer)
class FTPServerAdmin(admin.ModelAdmin):
    list_display = ("node", "bind_address", "port", "enabled")
    list_filter = ("enabled",)
    search_fields = ("node__hostname", "bind_address")
    actions = ("test_selected_servers",)

    def _get_test_hosts(self, server):
        bind_address = (server.bind_address or "").strip()
        wildcard_hosts = {"0.0.0.0", "::", "0:0:0:0:0:0:0:0"}
        hosts = []

        if bind_address and bind_address not in wildcard_hosts:
            hosts.append(bind_address)

        if server.node_id:
            for candidate in server.node.get_remote_host_candidates(resolve_dns=False):
                if candidate and candidate not in hosts:
                    hosts.append(candidate)

        if not hosts and bind_address in wildcard_hosts:
            if bind_address in {"::", "0:0:0:0:0:0:0:0"}:
                hosts.append("::1")
            else:
                hosts.append("127.0.0.1")

        return hosts

    @admin.action(description=_("Test selected servers"))
    def test_selected_servers(self, request, queryset):
        if not queryset.exists():
            self.message_user(
                request,
                _("No servers were selected."),
                level=messages.WARNING,
            )
            return

        tested = 0
        failed = 0
        skipped = 0

        for server in queryset.select_related("node"):
            hosts = self._get_test_hosts(server)
            if not hosts:
                skipped += 1
                self.message_user(
                    request,
                    _("Skipped %(server)s: no host available for testing.")
                    % {"server": server},
                    level=messages.WARNING,
                )
                continue

            last_error = None
            for host in hosts:
                try:
                    with socket.create_connection((host, server.port), timeout=5):
                        tested += 1
                        last_error = None
                        break
                except OSError as exc:
                    last_error = exc

            if last_error is not None:
                failed += 1
                self.message_user(
                    request,
                    _("Failed to reach %(server)s at %(hosts)s: %(error)s")
                    % {
                        "server": server,
                        "hosts": ", ".join(hosts),
                        "error": last_error,
                    },
                    level=messages.ERROR,
                )

        if tested:
            self.message_user(
                request,
                _("Tested %(count)s server(s).") % {"count": tested},
                level=messages.SUCCESS,
            )
        if failed:
            self.message_user(
                request,
                _("Failed to test %(count)s server(s).") % {"count": failed},
                level=messages.ERROR,
            )
        if skipped:
            self.message_user(
                request,
                _("Skipped %(count)s server(s).") % {"count": skipped},
                level=messages.WARNING,
            )


@admin.register(FTPFolder)
class FTPFolderAdmin(OwnableAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "node",
        "enabled",
        "user",
        "group",
        "owner_permission",
        "group_permission",
    )
    list_filter = ("enabled", "owner_permission", "group_permission")
    search_fields = ("name", "path", "node__hostname", "user__username")
    autocomplete_fields = ("node", "user", "group")
