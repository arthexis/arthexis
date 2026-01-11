from django.contrib import admin

from apps.core.admin import OwnableAdminMixin

from .models import FTPFolder, FTPServer


@admin.register(FTPServer)
class FTPServerAdmin(admin.ModelAdmin):
    list_display = ("node", "bind_address", "port", "enabled")
    list_filter = ("enabled",)
    search_fields = ("node__hostname", "bind_address")


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
