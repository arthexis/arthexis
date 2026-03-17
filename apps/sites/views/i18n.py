"""Language-selection views for site-aware localization."""

from __future__ import annotations

from django.views.i18n import set_language as django_set_language

from apps.sites.utils import get_site_allowed_language_codes, get_site_default_language_code


def set_language(request):
    """Set interface language while enforcing per-site language restrictions."""

    site = getattr(request, "site", None)
    allowed_codes = get_site_allowed_language_codes(site)
    default_code = get_site_default_language_code(site)

    requested_language = (request.POST.get("language") or "").strip().replace("_", "-").lower()[:15]
    if allowed_codes and requested_language not in allowed_codes:
        mutable_post = request.POST.copy()
        mutable_post["language"] = default_code
        request.POST = mutable_post

    return django_set_language(request)
