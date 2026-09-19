from django.contrib import admin

from apps.nodes.models import Node, NodeLink


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("identifier", "display_name", "role", "active")
    list_filter = ("role", "active")
    search_fields = ("identifier", "display_name")


@admin.register(NodeLink)
class NodeLinkAdmin(admin.ModelAdmin):
    list_display = ("source", "target", "relation", "created_at")
    list_filter = ("relation",)
