from django.contrib import admin

from .models import AgentSkill, AgentSkillFile


class AgentSkillFileInline(admin.TabularInline):
    model = AgentSkillFile
    extra = 0
    fields = (
        "relative_path",
        "portability",
        "included_by_default",
        "exclusion_reason",
        "size_bytes",
    )
    readonly_fields = ("size_bytes",)


@admin.register(AgentSkill)
class AgentSkillAdmin(admin.ModelAdmin):
    list_display = ("slug", "title")
    search_fields = ("slug", "title", "markdown")
    filter_horizontal = ("node_roles",)
    inlines = (AgentSkillFileInline,)


@admin.register(AgentSkillFile)
class AgentSkillFileAdmin(admin.ModelAdmin):
    list_display = (
        "skill",
        "relative_path",
        "portability",
        "included_by_default",
        "size_bytes",
    )
    list_filter = ("portability", "included_by_default")
    search_fields = ("skill__slug", "relative_path", "exclusion_reason", "content")
