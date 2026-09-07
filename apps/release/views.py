"""HTTP entry points for release-owned publishing flows."""

from django.contrib.admin.views.decorators import staff_member_required

from apps.release.domain import PUBLISH_STEPS


@staff_member_required
def release_progress(request, pk: int, action: str):
    """Render and advance release publish progress for staff users."""

    from apps.release.publishing.pipeline import release_progress_impl

    return release_progress_impl(request, pk, action)


__all__ = ["PUBLISH_STEPS", "release_progress"]
