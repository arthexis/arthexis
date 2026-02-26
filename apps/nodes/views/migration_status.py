"""Views for monitoring deferred node migration progress."""

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.nodes.models import NodeMigrationCheckpoint


@require_GET
def deferred_migration_status(request):
    """Return completion details for deferred node migration transforms."""

    checkpoint = NodeMigrationCheckpoint.objects.filter(
        key="nodes:legacy-data-cleanup"
    ).first()
    if not checkpoint:
        return JsonResponse(
            {
                "key": "nodes:legacy-data-cleanup",
                "total_items": 0,
                "processed_items": 0,
                "percent_complete": 0.0,
                "is_complete": False,
            }
        )

    return JsonResponse(
        {
            "key": checkpoint.key,
            "total_items": checkpoint.total_items,
            "processed_items": checkpoint.processed_items,
            "percent_complete": checkpoint.percent_complete(),
            "is_complete": checkpoint.is_complete,
            "updated_at": checkpoint.updated_at.isoformat(),
        }
    )
