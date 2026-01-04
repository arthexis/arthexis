from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.core.system import _systemd_unit_status


def _resolve_base_dir() -> Path:
    return Path(settings.BASE_DIR)


@admin.action(description=_("Validate Service Configuration"))
def validate_service_configuration(modeladmin, request, queryset):
    base_dir = _resolve_base_dir()
    for service in queryset:
        result = service.compare_to_installed(base_dir=base_dir)
        unit_name = result.get("unit_name") or service.unit_template
        status = result.get("status") or ""
        if result.get("matches"):
            message = _("%(unit)s matches the stored template.") % {"unit": unit_name}
            modeladmin.message_user(request, message, level=messages.SUCCESS)
        else:
            detail = status or _("Installed configuration differs from the template.")
            message = _("%(unit)s: %(detail)s") % {
                "unit": unit_name,
                "detail": detail,
            }
            modeladmin.message_user(request, message, level=messages.WARNING)


@admin.action(description=_("Validate Service is Active"))
def validate_service_active(modeladmin, request, queryset):
    base_dir = _resolve_base_dir()
    for service in queryset:
        context = service.build_context(base_dir=base_dir)
        unit_name = service.resolve_unit_name(context)
        if not unit_name:
            message = _("Could not resolve a unit name for %(service)s.") % {
                "service": service.display
            }
            modeladmin.message_user(request, message, level=messages.WARNING)
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

        modeladmin.message_user(request, message, level=level)
