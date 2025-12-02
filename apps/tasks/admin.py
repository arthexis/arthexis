from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _, ngettext

from apps.locals.user_data import EntityModelAdmin
from apps.tasks.forms import TaskCategoryAdminForm
from apps.tasks.models import ManualTask, TaskCategory


@admin.register(TaskCategory)
class TaskCategoryAdmin(EntityModelAdmin):
    form = TaskCategoryAdminForm
    list_display = (
        "name",
        "availability_label",
        "cost",
        "default_duration",
        "manager",
        "requestor_group",
        "assigned_group",
    )
    list_filter = (
        "availability",
        "requestor_group",
        "assigned_group",
        "manager",
    )
    search_fields = ("name", "description")
    raw_id_fields = ("requestor_group", "assigned_group", "manager")
    filter_horizontal = ("odoo_products",)
    fieldsets = (
        (None, {"fields": ("name", "description", "image")}),
        (
            _("Fulfillment"),
            {
                "fields": (
                    "availability",
                    "cost",
                    "default_duration",
                    "odoo_products",
                )
            },
        ),
        (
            _("Routing"),
            {"fields": ("requestor_group", "assigned_group", "manager")},
        ),
    )


@admin.register(ManualTask)
class ManualTaskAdmin(EntityModelAdmin):
    list_display = (
        "category",
        "assigned_user",
        "assigned_group",
        "manager",
        "node",
        "location",
        "scheduled_start",
        "scheduled_end",
        "enable_notifications",
    )
    list_filter = ("node", "location", "enable_notifications", "category")
    search_fields = (
        "description",
        "node__hostname",
        "location__name",
        "assigned_user__username",
        "assigned_user__email",
        "assigned_group__name",
        "manager__username",
        "category__name",
    )
    raw_id_fields = (
        "node",
        "location",
        "assigned_user",
        "assigned_group",
        "manager",
    )
    filter_horizontal = ("odoo_products",)
    date_hierarchy = "scheduled_start"
    actions = ("make_cp_reservations",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "category",
                    "description",
                    "odoo_products",
                    "duration",
                    "assigned_user",
                    "assigned_group",
                    "manager",
                    "enable_notifications",
                )
            },
        ),
        (
            _("Scope"),
            {
                "fields": (
                    "node",
                    "location",
                )
            },
        ),
        (
            _("Schedule"),
            {"fields": ("scheduled_start", "scheduled_end")},
        ),
    )

    @admin.action(description=_("Make Reservation at CP"))
    def make_cp_reservations(self, request, queryset):
        success_count = 0
        for task in queryset:
            try:
                task.create_cp_reservation()
            except ValidationError as exc:
                for message in self._normalize_validation_error(exc):
                    self.message_user(
                        request,
                        _("%(task)s: %(message)s")
                        % {"task": task, "message": message},
                        level=messages.WARNING,
                    )
            except Exception as exc:  # pragma: no cover - defensive guard
                self.message_user(
                    request,
                    _("%(task)s: %(error)s")
                    % {"task": task, "error": str(exc)},
                    level=messages.ERROR,
                )
            else:
                success_count += 1
        if success_count:
            message = ngettext(
                "Created %(count)d reservation.",
                "Created %(count)d reservations.",
                success_count,
            ) % {"count": success_count}
            self.message_user(request, message, level=messages.SUCCESS)

    @staticmethod
    def _normalize_validation_error(error: ValidationError) -> list[str]:
        messages_list: list[str] = []
        if error.message_dict:
            for field, values in error.message_dict.items():
                for value in values:
                    if field == "__all__":
                        messages_list.append(str(value))
                    else:
                        messages_list.append(f"{field}: {value}")
        elif error.messages:
            messages_list.extend(str(value) for value in error.messages)
        else:
            messages_list.append(str(error))
        return messages_list
