"""Admin registration for stored prompts."""

from django.contrib import admin

from .models import StoredPrompt


@admin.register(StoredPrompt)
class StoredPromptAdmin(admin.ModelAdmin):
    """Admin configuration for stored prompts."""

    list_display = ("title", "slug", "updated_at")
    search_fields = ("title", "slug", "prompt_text", "initial_plan")
    readonly_fields = ("created_at", "updated_at")
