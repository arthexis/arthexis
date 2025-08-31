from django.contrib import admin
from .models import GamePortal


@admin.register(GamePortal)
class GamePortalAdmin(admin.ModelAdmin):
    list_display = ("title", "slug")
