"""Admin integration for Raspberry Pi image artifacts."""

from django.contrib import admin

from apps.imager.models import RaspberryPiImageArtifact


@admin.register(RaspberryPiImageArtifact)
class RaspberryPiImageArtifactAdmin(admin.ModelAdmin):
    """Admin settings for generated Raspberry Pi artifacts."""

    list_display = ("name", "target", "output_filename", "download_uri", "created_at")
    list_filter = ("target", "created_at")
    search_fields = ("name", "target", "output_filename", "download_uri", "base_image_uri")
    readonly_fields = ("sha256", "size_bytes", "created_at", "updated_at")
