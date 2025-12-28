import logging
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template import loader
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from django.core.cache import cache

from apps.core import changelog
from apps.docs import views as docs_views
from apps.docs import rendering
from apps.groups.decorators import security_group_required
from apps.links.templatetags.ref_tags import build_footer_context
from apps.modules.models import Module
from apps.nodes.models import Node
from utils.sites import get_site

from ..forms import UserStoryForm
from ..utils import get_original_referer, get_request_language_code, landing

logger = logging.getLogger(__name__)


def _get_client_ip(request) -> str:
    """Return the client IP from the request headers."""

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        for value in forwarded_for.split(","):
            candidate = value.strip()
            if candidate:
                return candidate
    return request.META.get("REMOTE_ADDR", "")


@require_GET
def footer_fragment(request):
    """Return the footer markup for lazy-loading via HTMX."""

    force_footer = request.GET.get("force") in {"1", "true", "True"}
    context = build_footer_context(
        request=request,
        badge_site=getattr(request, "badge_site", None),
        badge_node=getattr(request, "badge_node", None),
        force_footer=force_footer,
    )
    return TemplateResponse(request, "core/footer.html", context)


@landing("Home")
@never_cache
def index(request):
    site = get_site(request)
    if site:
        badge = getattr(site, "badge", None)
        landing_page = getattr(badge, "landing_override", None)
        if landing_page is None:
            landing_page = getattr(site, "default_landing", None)
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
    return docs_views.render_readme_page(request, force_footer=True, role=role)


def sitemap(request):
    site = get_site(request)
    node = Node.get_local()
    role = node.role if node else None
    applications = Module.objects.for_role(role).filter(is_deleted=False)
    base = request.build_absolute_uri("/").rstrip("/")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    seen = set()
    for app in applications:
        loc = f"{base}{app.path}"
        if loc not in seen:
            seen.add(loc)
            lines.append(f"  <url><loc>{loc}</loc></url>")
    lines.append("</urlset>")
    return HttpResponse("\n".join(lines), content_type="application/xml")


@landing("Package Releases")
@security_group_required("Release Managers")
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


def changelog_report_data(request):
    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        return JsonResponse({"error": _("Invalid page number.")}, status=400)

    try:
        offset = int(request.GET.get("offset", "0"))
    except ValueError:
        return JsonResponse({"error": _("Invalid offset.")}, status=400)

    try:
        page_data = changelog.get_page(page_number, per_page=1, offset=offset)
    except changelog.ChangelogError as exc:
        return JsonResponse({"error": str(exc)}, status=503)

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


@require_POST
def submit_user_story(request):
    throttle_seconds = getattr(settings, "USER_STORY_THROTTLE_SECONDS", 300)
    client_ip = _get_client_ip(request)
    cache_key = None

    if throttle_seconds:
        cache_key = f"user-story:ip:{client_ip or 'unknown'}"
        if not cache.add(cache_key, timezone.now(), throttle_seconds):
            minutes = throttle_seconds // 60
            if throttle_seconds % 60:
                minutes += 1
            error_message = _(
                "You can only submit feedback once every %(minutes)s minutes."
            ) % {"minutes": minutes or 1}
            return JsonResponse(
                {"success": False, "errors": {"__all__": [error_message]}},
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

    form = UserStoryForm(data, user=request.user)
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
        return JsonResponse({"success": True})

    return JsonResponse({"success": False, "errors": form.errors}, status=400)


def csrf_failure(request, reason=""):
    """Custom CSRF failure view with a friendly message."""
    logger.warning("CSRF failure on %s: %s", request.path, reason)
    return render(request, "pages/csrf_failure.html", status=403)
