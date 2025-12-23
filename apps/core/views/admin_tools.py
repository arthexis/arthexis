from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.admin.sites import site as admin_site
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from apps.users import temp_passwords
from utils import revision


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

    version = ""
    version_path = Path(settings.BASE_DIR) / "VERSION"
    if version_path.exists():
        version = version_path.read_text(encoding="utf-8").strip()
    return JsonResponse(
        {
            "version": version,
            "revision": revision.get_revision(),
        }
    )
