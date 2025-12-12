import os
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.system import _systemd_unit_status
from apps.locals.user_data import EntityModelAdmin

from ..models import NodeService


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
    actions = ["validate_service_configuration", "validate_service_active"]

    @admin.display(description=_("Template"))
    def template_preview(self, obj):
        content = obj.get_template_body() if obj else ""
        if not content:
            return _("No template available.")
        return format_html('<pre style="white-space: pre-wrap;">{}</pre>', content)

    @admin.action(description=_("Validate Service Configuration"))
    def validate_service_configuration(self, request, queryset):
        service_dir = Path(os.environ.get("SYSTEMD_DIR", "/etc/systemd/system"))
        base_dir = Path(settings.BASE_DIR)
        for service in queryset:
            result = service.compare_to_installed(
                base_dir=base_dir, service_dir=service_dir
            )
            unit_name = result.get("unit_name") or service.unit_template
            status = result.get("status") or ""
            if result.get("matches"):
                message = _("%(unit)s matches the stored template.") % {
                    "unit": unit_name
                }
                self.message_user(request, message, level=messages.SUCCESS)
            else:
                detail = status or _("Installed configuration differs from the template.")
                message = _("%(unit)s: %(detail)s") % {
                    "unit": unit_name,
                    "detail": detail,
                }
                self.message_user(request, message, level=messages.WARNING)

    @admin.action(description=_("Validate Service is Active"))
    def validate_service_active(self, request, queryset):
        base_dir = Path(settings.BASE_DIR)
        for service in queryset:
            context = service.build_context(base_dir=base_dir)
            unit_name = service.resolve_unit_name(context)
            if not unit_name:
                message = _("Could not resolve a unit name for %(service)s.") % {
                    "service": service.display
                }
                self.message_user(request, message, level=messages.WARNING)
                continue

            status = _systemd_unit_status(unit_name)
            unit_status = status.get("status") or str(_("unknown"))
            enabled_state = status.get("enabled") or ""
            if status.get("missing"):
                message = _("%(unit)s is not installed.") % {"unit": unit_name}
                level = messages.WARNING
            elif unit_status == "active":
                message = _("%(unit)s is active.") % {"unit": unit_name}
                level = messages.SUCCESS
            else:
                detail = enabled_state or unit_status
                message = _("%(unit)s is %(status)s.") % {
                    "unit": unit_name,
                    "status": detail,
                }
                level = messages.WARNING

            self.message_user(request, message, level=level)
