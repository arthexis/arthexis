from django.contrib import admin

from .models import BskyAccount


@admin.register(BskyAccount)
class BskyAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "handle")
