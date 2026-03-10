"""Admin wiring for prototype records."""

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin
from apps.prototypes import prototype_ops
from apps.prototypes.models import Prototype


@admin.register(Prototype)
class PrototypeAdmin(EntityModelAdmin):
    """Expose local prototype records and activation helpers in admin."""

    list_display = (
        "slug",
        "name",
        "is_active",
        "port",
        "app_module",
        "sqlite_path",
    )
    list_filter = ("is_active", "is_deleted", "is_seed_data", "is_user_data")
    readonly_fields = ("is_active", "app_module", "app_label")
    search_fields = ("slug", "name", "description", "app_module")
    actions = ("activate_selected", "deactivate_selected")

    @admin.action(description=_("Activate selected prototype"))
    def activate_selected(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                _("Select exactly one prototype to activate."),
                level=messages.ERROR,
            )
            return

        prototype = queryset.first()
        assert prototype is not None
        prototype_ops.activate_prototype(prototype)
        self.message_user(
            request,
            _(
                "Activated %(prototype)s. Restart the suite or run "
                "`python manage.py prototype activate %(slug)s` for an automatic restart."
            )
            % {"prototype": prototype.name, "slug": prototype.slug},
            level=messages.SUCCESS,
        )

    @admin.action(description=_("Deactivate active prototype"))
    def deactivate_selected(self, request, queryset):
        if not Prototype.objects.filter(is_active=True).exists():
            self.message_user(
                request,
                _("No prototype is currently active."),
                level=messages.WARNING,
            )
            return

        prototype_ops.deactivate_prototype()
        self.message_user(
            request,
            _("Prototype activation has been cleared. Restart the suite to return to the base environment."),
            level=messages.SUCCESS,
        )
