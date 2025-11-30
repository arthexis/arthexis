from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from .models import MediaBucket, MediaFile


class MediaFileInline(admin.TabularInline):
    model = MediaFile
    extra = 0
    readonly_fields = ("download", "original_name", "content_type", "size", "uploaded_at")
    fields = ("download", "original_name", "content_type", "size", "uploaded_at")

    @admin.display(description=_("File"))
    def download(self, obj):
        if obj and obj.file:
            return format_html(
                '<a href="{url}" download>{name}</a>',
                url=obj.file.url,
                name=obj.original_name or obj.file.name,
            )
        return ""


@admin.register(MediaBucket)
class MediaBucketAdmin(EntityModelAdmin):
    list_display = ("name", "slug", "expires_at", "is_active", "max_bytes", "file_count")
    search_fields = ("name", "slug")
    readonly_fields = ("upload_endpoint", "created_at", "updated_at")
    inlines = [MediaFileInline]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "slug",
                    "upload_endpoint",
                    "allowed_patterns",
                    "max_bytes",
                    "expires_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description=_("Upload endpoint"))
    def upload_endpoint(self, obj):
        if not obj or not obj.pk:
            return ""
        path = reverse("ocpp:media-bucket-upload", kwargs={"slug": obj.slug})
        return format_html("<code>{}</code>", path)

    @admin.display(boolean=True, description=_("Active"))
    def is_active(self, obj):
        return not obj.is_expired()

    @admin.display(description=_("Files"))
    def file_count(self, obj):
        return obj.files.count()


@admin.register(MediaFile)
class MediaFileAdmin(EntityModelAdmin):
    list_display = ("original_name", "bucket", "size", "uploaded_at")
    search_fields = ("original_name", "bucket__name", "bucket__slug")
    readonly_fields = ("file_link", "original_name", "content_type", "size", "uploaded_at")
    fields = ("bucket", "file_link", "original_name", "content_type", "size", "uploaded_at")
    autocomplete_fields = ("bucket",)

    @admin.display(description=_("File"))
    def file_link(self, obj):
        if obj and obj.file:
            return format_html(
                '<a href="{url}" download>{name}</a>',
                url=obj.file.url,
                name=obj.original_name or obj.file.name,
            )
        return ""
