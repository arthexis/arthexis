"""HTTP entry points for the release publish flow."""

from django.contrib.admin.views.decorators import staff_member_required

from apps.release.publishing.pipeline import PUBLISH_STEPS, release_progress_impl


@staff_member_required
def release_progress(request, pk: int, action: str):
    """Render and advance release publish progress for staff users."""

    return release_progress_impl(request, pk, action)

__all__ = ["PUBLISH_STEPS", "release_progress"]
