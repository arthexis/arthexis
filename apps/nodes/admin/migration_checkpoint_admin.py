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

    def has_add_permission(self, request) -> bool:
        """Disallow checkpoint creation from admin for monitor-only access."""

        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        """Disallow checkpoint deletion from admin for monitor-only access."""

        return False

    def get_actions(self, request):
        """Hide bulk delete action for monitor-only access."""

        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


    @admin.display(description="Progress (%)")
    def completion_percent(self, obj: NodeMigrationCheckpoint) -> float:
        """Render percentage completion in the admin changelist."""

        return obj.percent_complete()
