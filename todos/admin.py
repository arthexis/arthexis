"""Admin configuration for todos."""

from django.contrib import admin

from .models import Todo


@admin.register(Todo)
class TodoAdmin(admin.ModelAdmin):
    list_display = ("text", "completed", "file_path", "line_number")
    list_filter = ("completed",)
