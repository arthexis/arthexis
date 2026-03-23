"""Admin wiring for retired prototype records."""

from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin
from apps.prototypes.models import Prototype


@admin.register(Prototype)
class PrototypeAdmin(EntityModelAdmin):
    """Expose prototype rows strictly as historical metadata in admin."""

    list_display = (
        "slug",
        "name",
        "is_runnable",
        "retired_at",
        "app_module",
        "port",
    )
    list_filter = ("is_runnable", "is_deleted", "is_seed_data", "is_user_data")
    readonly_fields = (
        "is_active",
        "is_runnable",
        "retired_at",
        "app_module",
        "app_label",
        "port",
        "sqlite_path",
        "sqlite_test_path",
        "cache_dir",
        "env_overrides",
        "created_at",
        "updated_at",
    )
    search_fields = ("slug", "name", "description", "app_module", "retirement_notes")
    fields = (
        "slug",
        "name",
        "description",
        "retirement_notes",
        "is_runnable",
        "retired_at",
        "is_active",
        "app_module",
        "app_label",
        "port",
        "sqlite_path",
        "sqlite_test_path",
        "cache_dir",
        "env_overrides",
        "created_at",
        "updated_at",
        "is_seed_data",
        "is_user_data",
        "is_deleted",
    )
    actions = ()

    def get_actions(self, request):
        """Disable inherited bulk actions for retired prototype records."""

        return {}
