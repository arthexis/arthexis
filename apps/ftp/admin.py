from django.contrib import admin

from .models import FTPFolder, FTPServer


@admin.register(FTPServer)
class FTPServerAdmin(admin.ModelAdmin):
    list_display = ("node", "bind_address", "port", "enabled")
    list_filter = ("enabled",)
    search_fields = ("node__hostname", "bind_address")


@admin.register(FTPFolder)
class FTPFolderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "node",
        "enabled",
        "owner",
        "security_group",
        "owner_permission",
        "group_permission",
    )
    list_filter = ("enabled", "owner_permission", "group_permission")
    search_fields = ("name", "path", "node__hostname", "owner__username")
    autocomplete_fields = ("node", "owner", "security_group")
