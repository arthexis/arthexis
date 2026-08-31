from django.contrib import admin

from .models import CharacterScreen, PixelScreen


@admin.register(CharacterScreen)
class CharacterScreenAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "skin",
        "columns",
        "rows",
        "resolution_width",
        "resolution_height",
        "min_refresh_ms",
    )
    search_fields = ("name", "slug", "skin")


@admin.register(PixelScreen)
class PixelScreenAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "skin",
        "columns",
        "rows",
        "resolution_width",
        "resolution_height",
        "pixel_format",
        "bytes_per_pixel",
        "min_refresh_ms",
    )
    search_fields = ("name", "slug", "skin")
