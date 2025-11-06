from utils.sites import get_site
from django.urls import Resolver404, resolve
from django.shortcuts import resolve_url
from django.conf import settings
from pathlib import Path
from nodes.models import Node
from core.models import Reference
from core.reference_utils import filter_visible_references
from .models import Module

_FAVICON_DIR = Path(settings.BASE_DIR) / "pages" / "fixtures" / "data"
_FAVICON_FILENAMES = {
    "default": "favicon.txt",
    "Watchtower": "favicon_watchtower.txt",
    "Constellation": "favicon_watchtower.txt",
    "Control": "favicon_control.txt",
    "Satellite": "favicon_satellite.txt",
}


def _load_favicon(filename: str) -> str:
    path = _FAVICON_DIR / filename
    try:
        return f"data:image/png;base64,{path.read_text().strip()}"
    except OSError:
        return ""


_DEFAULT_FAVICON = _load_favicon(_FAVICON_FILENAMES["default"])
_ROLE_FAVICONS = {
    role: (_load_favicon(filename) or _DEFAULT_FAVICON)
    for role, filename in _FAVICON_FILENAMES.items()
    if role != "default"
}


def nav_links(request):
    """Provide navigation links for the current site."""
    site = get_site(request)
    node = Node.get_local()
    role = node.role if node else None
    if role:
        modules = (
            Module.objects.filter(node_role=role, is_deleted=False)
            .select_related("application")
            .prefetch_related("landings")
        )
    else:
        modules = []

    valid_modules = []
    current_module = None
    user = getattr(request, "user", None)
    user_is_authenticated = getattr(user, "is_authenticated", False)
    user_is_superuser = getattr(user, "is_superuser", False)
    if user_is_authenticated:
        user_group_names = set(user.groups.values_list("name", flat=True))
    else:
        user_group_names = set()
    for module in modules:
        landings = []
        for landing in module.landings.filter(enabled=True):
            try:
                match = resolve(landing.path)
            except Resolver404:
                continue
            view_func = match.func
            requires_login = bool(getattr(view_func, "login_required", False))
            if not requires_login and hasattr(view_func, "login_url"):
                requires_login = True
            staff_only = getattr(view_func, "staff_required", False)
            required_groups = getattr(
                view_func, "required_security_groups", frozenset()
            )
            blocked_reason = None
            if required_groups:
                requires_login = True
                if not user_is_authenticated:
                    blocked_reason = "login"
                elif not user_is_superuser and not (
                    user_group_names & set(required_groups)
                ):
                    blocked_reason = "permission"
            elif requires_login and not user_is_authenticated:
                blocked_reason = "login"

            if staff_only and not getattr(request.user, "is_staff", False):
                if blocked_reason != "login":
                    blocked_reason = "permission"

            landing.nav_is_locked = bool(blocked_reason)
            landing.nav_lock_reason = blocked_reason
            landings.append(landing)
        if landings:
            normalized_module_path = module.path.rstrip("/") or "/"
            if normalized_module_path == "/read":
                primary_landings = [
                    landing
                    for landing in landings
                    if landing.path.rstrip("/") == normalized_module_path
                ]
                if primary_landings:
                    landings = primary_landings
                else:
                    landings = [landings[0]]
            app_name = getattr(module.application, "name", "").lower()
            if app_name == "awg":
                module.menu = "Calculators"
            elif module.path.rstrip("/").lower() == "/man":
                module.menu = "Manual"
            module.enabled_landings = landings
            valid_modules.append(module)
            if request.path.startswith(module.path):
                if current_module is None or len(module.path) > len(
                    current_module.path
                ):
                    current_module = module


    valid_modules.sort(key=lambda m: m.menu_label.lower())

    if current_module and current_module.favicon:
        favicon_url = current_module.favicon.url
    else:
        favicon_url = None
        if site:
            try:
                if site.badge.favicon:
                    favicon_url = site.badge.favicon.url
            except Exception:
                pass
        if not favicon_url:
            role_name = getattr(getattr(node, "role", None), "name", "")
            favicon_url = _ROLE_FAVICONS.get(role_name, _DEFAULT_FAVICON) or _DEFAULT_FAVICON

    header_refs_qs = (
        Reference.objects.filter(show_in_header=True)
        .exclude(value="")
        .prefetch_related("roles", "features", "sites")
    )
    header_references = filter_visible_references(
        header_refs_qs,
        request=request,
        site=site,
        node=node,
    )

    return {
        "nav_modules": valid_modules,
        "favicon_url": favicon_url,
        "header_references": header_references,
        "login_url": resolve_url(settings.LOGIN_URL),
    }
