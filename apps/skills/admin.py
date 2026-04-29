from django.contrib import admin

from .models import AgentSkill


@admin.register(AgentSkill)
class AgentSkillAdmin(admin.ModelAdmin):
    list_display = ("slug", "title")
    search_fields = ("slug", "title", "markdown")
    filter_horizontal = ("node_roles",)
