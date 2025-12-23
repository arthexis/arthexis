from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from ..models import NodeService
from .actions import validate_service_active, validate_service_configuration


@admin.register(NodeService)
class NodeServiceAdmin(EntityModelAdmin):
    list_display = ("display", "unit_template", "is_required", "feature")
    list_filter = ("is_required", "feature")
    search_fields = ("display", "slug", "unit_template")
    autocomplete_fields = ("feature",)
    readonly_fields = ("template_preview",)
    fields = (
        "display",
        "slug",
        "unit_template",
        "feature",
        "is_required",
        "template_path",
        "template_content",
        "template_preview",
    )
    actions = [validate_service_configuration, validate_service_active]

    @admin.display(description=_("Template"))
    def template_preview(self, obj):
        content = obj.get_template_body() if obj else ""
        if not content:
            return _("No template available.")
        return format_html('<pre style="white-space: pre-wrap;">{}</pre>', content)
