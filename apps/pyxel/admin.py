from django.contrib import admin

from .models import PyxelViewport


@admin.register(PyxelViewport)
class PyxelViewportAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "skin",
        "columns",
        "rows",
        "resolution_width",
        "resolution_height",
        "pyxel_fps",
    )
    search_fields = ("name", "slug", "skin", "pyxel_script")
