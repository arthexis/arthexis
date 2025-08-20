from django.contrib import admin
from django.utils.html import format_html

from .models import Reference


@admin.register(Reference)
class ReferenceAdmin(admin.ModelAdmin):
    list_display = ("value", "include_in_footer")
    readonly_fields = ("uses", "qr_code")
    fields = (
        "value",
        "alt_text",
        "method",
        "include_in_footer",
        "uses",
        "qr_code",
    )

    def qr_code(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" alt="{}" style="height:200px;"/>',
                obj.image.url,
                obj.alt_text or obj.value,
            )
        return ""

    qr_code.short_description = "QR Code"
