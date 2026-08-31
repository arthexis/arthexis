import logging
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template import loader
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_GET, require_POST

from apps.core import changelog
from apps.docs import rendering
from apps.docs import views as docs_views
from apps.features.utils import is_suite_feature_enabled
from apps.groups.constants import AP_USER_GROUP_NAME
from apps.modules.models import Module
from apps.nodes.models import Node
from apps.nodes.utils import FeatureChecker
from apps.ocpp.utils.websocket import resolve_ws_scheme
from apps.sites.templatetags.site_footer import build_footer_context
from utils.decorators import security_group_required, staff_required
from utils.sites import get_site

from ..favicons import FAVICON_CONTENT_TYPE, FAVICON_FILENAMES, load_favicon_bytes
from ..forms import UserStoryForm
from ..utils import (
    get_original_referer,
    get_referrer_landing,
    get_request_language_code,
    landing,
)

logger = logging.getLogger(__name__)


SUPERUSER_USER_STORY_THROTTLE_SECONDS = 30
STAFF_USER_STORY_THROTTLE_SECONDS = 120


_SUPPORTED_OCPP_VERSIONS: tuple[str, ...] = (
    "1.6J",
    "2.0.1",
    "2.1",
)


def _format_operator_ws_endpoint_host(request, site) -> str:
    """Return a websocket host value suitable for the operator notice endpoint."""

    raw_host = request.get_host()
    parsed = urlsplit(f"//{raw_host}")
    hostname = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return raw_host

    if hostname is None:
        return raw_host

    if port is None:
        return raw_host

    if port in {80, 443}:
        return hostname

    profile = getattr(site, "profile", None) if site else None
    if profile is not None and bool(profile.managed):
        return hostname

    return raw_host


def _get_client_ip(request) -> str:
    """Return the client IP from the request headers."""

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        for value in forwarded_for.split(","):
            candidate = value.strip()
            if candidate:
                return candidate
    return request.META.get("REMOTE_ADDR", "")


def _get_site_favicon_url(request) -> str:
    site = get_site(request)
    if not site:
        return ""
    try:
        return site.badge.favicon_url
    except (AttributeError, DatabaseError, ObjectDoesNotExist):
        return ""


def _get_role_favicon_filename() -> str:
    try:
        node = Node.get_local()
        role_name = getattr(getattr(node, "role", None), "name", "")
    except (DatabaseError, ObjectDoesNotExist):
        role_name = ""
    return FAVICON_FILENAMES.get(role_name, FAVICON_FILENAMES["default"])


def _is_same_favicon_target(request, target_url: str) -> bool:
    parsed = urlsplit(
        urljoin(f"{request.scheme}://{request.get_host()}{request.path}", target_url)
    )
    target_path = parsed.path or "/"
    if target_path != request.path:
        return False

    request_host = urlsplit(f"//{request.get_host()}")
    target_scheme = parsed.scheme or request.scheme
    request_scheme = request.scheme
    try:
        target_port = parsed.port
        request_port = request_host.port
    except ValueError:
        return False
    target_port = target_port or _default_favicon_port(target_scheme)
    request_port = request_port or _default_favicon_port(request_scheme)
    return (
        (parsed.hostname or "").lower() == (request_host.hostname or "").lower()
        and target_port == request_port
    )


def _default_favicon_port(scheme: str) -> int | None:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


@require_GET
def favicon(request):
    """Serve the conventional browser favicon route."""

    site_favicon_url = _get_site_favicon_url(request)
    if site_favicon_url and not _is_same_favicon_target(request, site_favicon_url):
        return redirect(site_favicon_url)

    role_filename = _get_role_favicon_filename()
    content = load_favicon_bytes(role_filename)
    if not content and role_filename != FAVICON_FILENAMES["default"]:
        content = load_favicon_bytes(FAVICON_FILENAMES["default"])
    if not content:
        raise Http404("Favicon is not configured")

    response = HttpResponse(content, content_type=FAVICON_CONTENT_TYPE)
    patch_cache_control(response, public=True, max_age=86400)
    return response


@require_GET
def footer_fragment(request):
    """Return the footer markup for lazy-loading via HTMX."""

    force_footer = request.GET.get("force") in {"1", "true", "True"}
    context = build_footer_context(
        request=request,
        badge_site=getattr(request, "badge_site", None),
        badge_node=getattr(request, "badge_node", None),
        force_footer=force_footer,
        module=getattr(request, "current_module", None),
    )
    return TemplateResponse(request, "core/footer.html", context)


def _operator_interface_notice_context(request, site):
    """Build the template context for the OCPP operator interface notice."""

    ws_host = _format_operator_ws_endpoint_host(request, site)
    ws_scheme = resolve_ws_scheme(request=request)
    return {
        "ocpp_versions": _SUPPORTED_OCPP_VERSIONS,
        "ws_endpoint": f"{ws_scheme}://{ws_host}/<charge_point_id>/",
    }


@require_GET
def operator_interface_notice(request):
    """Render a minimal vendor-facing notice for OCPP websocket onboarding."""

    site = get_site(request)
    return TemplateResponse(
        request,
        "pages/operator_interface_notice.html",
        _operator_interface_notice_context(request, site),
    )


def _render_operator_interface_fallback(request, site):
    """Render the OCPP-facing fallback notice used for disabled operator interfaces."""

    return TemplateResponse(
        request,
        "pages/operator_interface_notice.html",
        _operator_interface_notice_context(request, site),
    )


@landing("Home")
def index(request):
    """Render the public home page or interface fallback when configured."""

    site = get_site(request)
    interface_enabled = is_suite_feature_enabled(
        "operator-site-interface", default=True
    )
    if not interface_enabled:
        site_profile = getattr(site, "profile", None) if site else None
        interface_landing = (
            getattr(site_profile, "interface_landing", None) if site_profile else None
        )
        if (
            interface_landing
            and not getattr(interface_landing, "is_deleted", False)
            and interface_landing.enabled
        ):
            target_path = interface_landing.path
            if target_path:
                from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

                if not url_has_allowed_host_and_scheme(
                    url=target_path,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    logger.warning(
                        "Blocked unsafe operator interface redirect for site %s: %s",
                        getattr(site, "pk", None),
                        target_path,
                    )
                    return _render_operator_interface_fallback(request, site)

                parsed_target = urlparse(target_path)
                params = parse_qs(parsed_target.query, keep_blank_values=True)
                params["operator_interface"] = ["1"]
                rebuilt_target = parsed_target._replace(
                    query=urlencode(params, doseq=True)
                )
                redirect_target = urlunparse(rebuilt_target)
                if redirect_target != request.get_full_path():
                    return redirect(redirect_target)
        return _render_operator_interface_fallback(request, site)

    if site:
        referrer_landing = get_referrer_landing(request, site)
        skip_default_landing = False
        if referrer_landing:
            referrer_page = referrer_landing.landing
            if (
                referrer_page
                and not getattr(referrer_page, "is_deleted", False)
                and referrer_page.enabled
            ):
                target_path = referrer_page.path
                if target_path and target_path != request.path:
                    return redirect(target_path)
            else:
                skip_default_landing = True
        if not skip_default_landing:
            badge = getattr(site, "badge", None)
            landing_page = getattr(badge, "landing_override", None)
            if landing_page is None:
                landing_page = getattr(
                    getattr(site, "profile", None), "default_landing", None
                )
            if (
                landing_page
                and not getattr(landing_page, "is_deleted", False)
                and landing_page.enabled
            ):
                target_path = landing_page.path
                if target_path and target_path != request.path:
                    return redirect(target_path)
    node = Node.get_local()
    role = node.role if node else None
    response = docs_views.render_readme_page(request, force_footer=True, role=role)
    if not request.user.is_authenticated:
        patch_cache_control(response, public=True, max_age=300, s_maxage=300)
    return response


def sitemap(request):
    node = Node.get_local()
    role = node.role if node else None
    role_id = getattr(role, "id", "none")
    base = request.build_absolute_uri("/").rstrip("/")
    cache_key = f"sitemap:role:{role_id}:{base}"
    cached = cache.get(cache_key)
    if cached:
        return HttpResponse(cached, content_type="application/xml")
    applications = (
        Module.objects.for_role(role)
        .filter(is_deleted=False)
        .prefetch_related("features")
    )
    feature_checker = FeatureChecker()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    seen = set()
    for app in applications:
        if not app.meets_feature_requirements(feature_checker.is_enabled):
            continue
        loc = f"{base}{app.path}"
        if loc not in seen:
            seen.add(loc)
            lines.append(f"  <url><loc>{loc}</loc></url>")
    lines.append("</urlset>")
    xml_content = "\n".join(lines)
    cache.set(cache_key, xml_content, timeout=300)
    return HttpResponse(xml_content, content_type="application/xml")


@landing("Package Releases")
@staff_required
def release_checklist(request):
    file_path = Path(settings.BASE_DIR) / "releases" / "release-checklist.md"
    if not file_path.exists():
        raise Http404("Release checklist not found")
    text = file_path.read_text(encoding="utf-8")
    html, toc_html = rendering.render_markdown_with_toc(text)
    context = {"content": html, "title": "Release Checklist", "toc": toc_html}
    response = render(request, "docs/readme.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


@landing(_("Changelog"))
def changelog_report(request):
    try:
        initial_page = changelog.get_initial_page()
    except changelog.ChangelogError as exc:
        initial_sections = tuple()
        has_more = False
        next_page = None
        error_message = str(exc)
    else:
        initial_sections = initial_page.sections
        has_more = initial_page.has_more
        next_page = initial_page.next_page
        error_message = ""

    context = {
        "title": _("Changelog"),
        "initial_sections": initial_sections,
        "has_more_sections": has_more,
        "next_page": next_page,
        "initial_section_count": len(initial_sections),
        "error_message": error_message,
        "loading_label": _("Loading more updates…"),
        "error_label": _("Unable to load additional updates."),
        "complete_label": _("You're all caught up."),
    }
    response = render(request, "pages/changelog.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


@landing(_("Access Point Visitors"))
@security_group_required(AP_USER_GROUP_NAME)
def visitors(request):
    return render(request, "pages/visitors.html")


def changelog_report_data(request):
    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        return JsonResponse({"error": _("Invalid page number.")}, status=400)
    if page_number < 1:
        return JsonResponse({"error": _("Invalid page number.")}, status=400)

    try:
        offset = int(request.GET.get("offset", "0"))
    except ValueError:
        return JsonResponse({"error": _("Invalid offset.")}, status=400)
    if offset < 0:
        return JsonResponse({"error": _("Invalid offset.")}, status=400)

    try:
        page_data = changelog.get_page(page_number, per_page=1, offset=offset)
    except changelog.ChangelogError:
        logger.exception(
            "Failed to load public changelog page %s (offset %s)", page_number, offset
        )
        return JsonResponse(
            {"error": _("Unable to load additional updates.")}, status=500
        )

    if not page_data.sections:
        return JsonResponse({"html": "", "has_more": False, "next_page": None})

    html = loader.render_to_string(
        "includes/changelog/section_list.html",
        {"sections": page_data.sections, "variant": "public"},
        request=request,
    )
    return JsonResponse(
        {"html": html, "has_more": page_data.has_more, "next_page": page_data.next_page}
    )


def _get_user_story_throttle_seconds(request) -> int:
    throttle_seconds = int(getattr(settings, "USER_STORY_THROTTLE_SECONDS", 300) or 0)
    user = getattr(request, "user", None)
    if throttle_seconds and bool(getattr(user, "is_superuser", False)):
        return min(throttle_seconds, SUPERUSER_USER_STORY_THROTTLE_SECONDS)
    if throttle_seconds and bool(getattr(user, "is_staff", False)):
        return min(throttle_seconds, STAFF_USER_STORY_THROTTLE_SECONDS)
    return throttle_seconds


def _get_user_story_user_identifier(user) -> str:
    user_identifier = getattr(user, "pk", None) or getattr(user, "id", None)
    if user_identifier is None and hasattr(user, "get_username"):
        user_identifier = user.get_username()
    return str(user_identifier or "unknown")


def _get_user_story_throttle_cache_key(request, client_ip: str) -> str:
    user = getattr(request, "user", None)
    if bool(getattr(user, "is_superuser", False)):
        return f"user-story:superuser:{_get_user_story_user_identifier(user)}"
    if bool(getattr(user, "is_staff", False)):
        return f"user-story:staff:{_get_user_story_user_identifier(user)}"
    return f"user-story:ip:{client_ip or 'unknown'}"


def _format_user_story_throttle_error(throttle_seconds: int) -> str:
    if throttle_seconds < 60:
        seconds = throttle_seconds or 1
        return ngettext(
            "You can only submit feedback once every %(seconds)s second.",
            "You can only submit feedback once every %(seconds)s seconds.",
            seconds,
        ) % {"seconds": seconds}
    minutes = throttle_seconds // 60
    if throttle_seconds % 60:
        minutes += 1
    minutes = minutes or 1
    return ngettext(
        "You can only submit feedback once every %(minutes)s minute.",
        "You can only submit feedback once every %(minutes)s minutes.",
        minutes,
    ) % {"minutes": minutes}


def _feedback_issue_label_tags_allowed(request) -> bool:
    user = getattr(request, "user", None)
    return bool(getattr(user, "is_superuser", False))


@require_POST
def submit_user_story(request):
    if not is_suite_feature_enabled("feedback-ingestion", default=True):
        return JsonResponse(
            {
                "success": False,
                "errors": {"__all__": [_("Feedback ingestion is disabled.")]},
            },
            status=404,
        )

    throttle_seconds = _get_user_story_throttle_seconds(request)
    client_ip = _get_client_ip(request)

    if throttle_seconds:
        cache_key = _get_user_story_throttle_cache_key(request, client_ip)
        if not cache.add(cache_key, timezone.now(), throttle_seconds):
            return JsonResponse(
                {
                    "success": False,
                    "errors": {
                        "__all__": [_format_user_story_throttle_error(throttle_seconds)]
                    },
                },
                status=429,
            )

    data = request.POST.copy()
    anonymous_placeholder = ""
    if request.user.is_authenticated:
        data["name"] = request.user.get_username()[:40]
    elif not data.get("name"):
        anonymous_placeholder = "anonymous@example.invalid"
        data["name"] = anonymous_placeholder
    if not data.get("path"):
        data["path"] = request.get_full_path()

    form = UserStoryForm(data, files=request.FILES, user=request.user)
    if request.user.is_authenticated:
        form.instance.user = request.user

    if form.is_valid():
        story = form.save(commit=False)
        if anonymous_placeholder and story.name == anonymous_placeholder:
            story.name = ""
        if request.user.is_authenticated:
            story.user = request.user
            story.owner = request.user
            story.name = request.user.get_username()[:40]
        if not story.name:
            story.name = str(_("Anonymous"))[:40]
        story.path = (story.path or request.get_full_path())[:500]
        story.allow_feedback_issue_label_tags = _feedback_issue_label_tags_allowed(
            request
        )
        story.referer = get_original_referer(request)
        story.user_agent = request.META.get("HTTP_USER_AGENT", "")
        story.ip_address = client_ip or None
        story.is_user_data = True
        language_code = getattr(request, "selected_language_code", "")
        if not language_code:
            language_code = get_request_language_code(request)
        if language_code:
            story.language_code = language_code
        story.save()
        form.save_attachments()
        if request.user.is_authenticated:
            form.update_chat_preference(
                owner=request.user,
                contact_via_chat=bool(form.cleaned_data.get("contact_via_chat")),
            )
        return JsonResponse({"success": True})

    return JsonResponse({"success": False, "errors": form.errors}, status=400)


def csrf_failure(request, reason=""):
    """Custom CSRF failure view with a friendly message."""
    logger.warning("CSRF failure on %s: %s", request.path, reason)
    return render(request, "pages/csrf_failure.html", status=403)
