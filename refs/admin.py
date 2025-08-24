from django.contrib import admin
from django.utils.html import format_html

from .models import Reference, Tag, TaggedItem


@admin.register(Reference)
class ReferenceAdmin(admin.ModelAdmin):
    list_display = ("alt_text", "content_type", "include_in_footer", "author")
    readonly_fields = ("uses", "qr_code", "author")
    fields = (
        "alt_text",
        "content_type",
        "value",
        "file",
        "method",
        "include_in_footer",
        "author",
        "uses",
        "qr_code",
    )

    def qr_code(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" alt="{}" style="height:200px;"/>',
                obj.image.url,
                obj.alt_text,
            )
        return ""

    qr_code.short_description = "QR Code"


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name", "slug"]


@admin.register(TaggedItem)
class TaggedItemAdmin(admin.ModelAdmin):
    list_display = ("tag", "content_type", "object_id")
    list_filter = ("tag", "content_type")
