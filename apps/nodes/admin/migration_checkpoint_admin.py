"""Admin registration for deferred node migration checkpoints."""

from django.contrib import admin

from apps.nodes.models import NodeMigrationCheckpoint


@admin.register(NodeMigrationCheckpoint)
class NodeMigrationCheckpointAdmin(admin.ModelAdmin):
    """Expose deferred migration progress to operators."""

    list_display = (
        "key",
        "processed_items",
        "total_items",
        "is_complete",
        "completion_percent",
        "updated_at",
    )
    readonly_fields = (
        "key",
        "processed_items",
        "total_items",
        "is_complete",
        "completion_percent",
        "updated_at",
    )
    ordering = ("key",)

    @admin.display(description="Progress (%)")
    def completion_percent(self, obj: NodeMigrationCheckpoint) -> float:
        """Render percentage completion in the admin changelist."""

        return obj.percent_complete()
