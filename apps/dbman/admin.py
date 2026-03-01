from django.contrib import admin
from django.db.utils import OperationalError, ProgrammingError

from .models import ManagedDatabase


@admin.register(ManagedDatabase)
class ManagedDatabaseAdmin(admin.ModelAdmin):
    """Admin surface for viewing configured and external databases."""

    list_display = (
        "alias",
        "display_name",
        "engine",
        "name",
        "host",
        "port",
        "username",
        "is_current",
        "is_django_connection",
        "updated_at",
    )
    list_filter = ("is_current", "is_django_connection")
    search_fields = ("alias", "display_name", "engine", "name", "host", "username")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        """Ensure configured Django databases are visible on the changelist."""

        try:
            ManagedDatabase.sync_from_settings()
        except (OperationalError, ProgrammingError):
            # Tables may not exist yet during first migration runs.
            pass

        return super().get_queryset(request)
