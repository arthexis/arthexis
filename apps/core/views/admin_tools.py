"""Core runtime information view with user-view compatibility exports."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db.utils import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.sites.utils import user_in_site_operator_group
from apps.users.admin_views import request_temp_password, stop_impersonation
from utils import revision
from utils.version import get_version


@require_GET
def version_info(request):
    """Return the running application version and Git revision."""

    user = getattr(request, "user", None)
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        allowed = True
    elif not getattr(user, "is_authenticated", False):
        allowed = False
    else:
        try:
            allowed = user_in_site_operator_group(user)
        except (OperationalError, ProgrammingError):
            allowed = False
    if not allowed:
        raise PermissionDenied
    return JsonResponse(
        {
            "version": get_version(),
            "revision": revision.get_revision(),
        }
    )


__all__ = ["request_temp_password", "stop_impersonation", "version_info"]
