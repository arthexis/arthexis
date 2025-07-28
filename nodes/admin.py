from django.contrib import admin

from .models import Node


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("hostname", "address", "port", "last_seen")
    search_fields = ("hostname", "address")
