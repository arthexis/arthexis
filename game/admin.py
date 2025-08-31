from django.contrib import admin
from .models import GamePortal, GameMaterial, MaterialRegion


@admin.register(GamePortal)
class GamePortalAdmin(admin.ModelAdmin):
    list_display = ("title", "slug")


@admin.register(GameMaterial)
class GameMaterialAdmin(admin.ModelAdmin):
    list_display = ("slug",)


@admin.register(MaterialRegion)
class MaterialRegionAdmin(admin.ModelAdmin):
    list_display = ("material", "name", "target")
