from django.contrib import admin

from .models import Sample


@admin.register(Sample)
class SampleAdmin(admin.ModelAdmin):
    list_display = ("created_at", "short_content")
    readonly_fields = ("created_at",)

    def short_content(self, obj):
        return obj.content[:50]

    short_content.short_description = "Content"
