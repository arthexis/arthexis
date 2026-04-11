from __future__ import annotations

from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login, logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.core.impersonation import clear_impersonator_user_id, get_impersonator_user_id
from apps.users import temp_passwords
from apps.users.models import User
from utils import revision
from utils.version import get_version


@staff_member_required
@require_GET
def request_temp_password(request):
    """Generate a temporary password for the authenticated staff member."""

    user = request.user
    username = user.get_username()
    password = temp_passwords.generate_password()
    entry = temp_passwords.store_temp_password(
        username,
        password,
        allow_change=True,
    )
    context = {
        **admin_site.each_context(request),
        "title": _("Temporary password"),
        "username": username,
        "password": password,
        "expires_at": timezone.localtime(entry.expires_at),
        "allow_change": entry.allow_change,
        "return_url": reverse("admin:password_change"),
    }
    return TemplateResponse(
        request,
        "admin/core/request_temp_password.html",
        context,
    )


@staff_member_required
@require_GET
def version_info(request):
    """Return the running application version and Git revision."""

    return JsonResponse(
        {
            "version": get_version(),
            "revision": revision.get_revision(),
        }
    )


def _safe_next_url(request, *, fallback_url: str) -> str:
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback_url


@require_POST
def stop_impersonation(request):
    """Restore the original admin account when the session is impersonating."""

    redirect_url = _safe_next_url(request, fallback_url=reverse("admin:index"))
    impersonator_id = get_impersonator_user_id(getattr(request, "session", None))
    if impersonator_id is None:
        return redirect(redirect_url)

    impersonator = User.all_objects.filter(pk=impersonator_id, is_active=True).first()
    clear_impersonator_user_id(request.session)

    if impersonator is not None:
        login(request, impersonator, backend="apps.users.backends.PasswordOrOTPBackend")
    else:
        logout(request)

    return redirect(redirect_url)
