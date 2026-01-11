from utils.sites import get_site
from django.urls import Resolver404, resolve
from django.shortcuts import resolve_url
from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError
from pathlib import Path
from apps.nodes.models import Node, NodeFeature
from apps.groups.models import SecurityGroup
from apps.links.models import Reference
from apps.links.reference_utils import filter_visible_references
from apps.modules.models import Module
from .models import SiteTemplate

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
    site = getattr(request, "badge_site", None) or get_site(request)
    node = getattr(request, "badge_node", None)
    role = getattr(request, "badge_role", None)
    try:
        if node is None:
            node = Node.get_local()
        if role is None:
            role = node.role if node else None
    except (OperationalError, ProgrammingError):
        node = node or None
        role = role or None
    request.badge_site = site
    request.badge_node = node
    request.badge_role = role

    try:
        modules = (
            Module.objects.for_role(role)
            .filter(is_deleted=False)
            .select_related("application", "security_group")
            .prefetch_related("landings", "roles")
        )
    except (OperationalError, ProgrammingError):
        modules = []

    try:
        modules = list(modules)
    except (OperationalError, ProgrammingError):
        modules = []

    valid_modules = []
    current_module = None
    user = getattr(request, "user", None)
    user_is_authenticated = getattr(user, "is_authenticated", False)
    user_is_superuser = getattr(user, "is_superuser", False)
    if user_is_authenticated:
        user_group_names = set(user.groups.values_list("name", flat=True))
        user_group_ids = set(user.groups.values_list("id", flat=True))
    else:
        user_group_names = set()
        user_group_ids = set()
    feature_cache: dict[str, bool] = {}

    def feature_is_enabled(slug: str) -> bool:
        if slug in feature_cache:
            return feature_cache[slug]
        try:
            feature = NodeFeature.objects.filter(slug=slug).first()
        except (OperationalError, ProgrammingError):
            feature = None
        try:
            enabled = bool(feature and feature.is_enabled)
        except Exception:
            enabled = False
        feature_cache[slug] = enabled
        return enabled

    for module in modules:
        module_roles = getattr(module, "roles")
        role_ids = {r.id for r in module_roles.all()} if module_roles else set()
        role_matches = not role_ids or (role and role.id in role_ids)
        group_matches = bool(
            module.security_group_id
            and user_is_authenticated
            and module.security_group_id in user_group_ids
        )
        if module.security_group_id:
            if module.security_mode == Module.SECURITY_EXCLUSIVE:
                if not (role_matches and group_matches):
                    continue
            else:
                if not (role_matches or group_matches):
                    continue
        elif not role_matches:
            continue
        landings = []
        seen_paths: set[str] = set()
        for landing in module.landings.filter(enabled=True):
            normalized_path = landing.path.rstrip("/") or "/"
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            landing.nav_is_invalid = not landing.is_link_valid()
            landing.nav_is_locked = False
            landing.nav_lock_reason = None
            try:
                match = resolve(landing.path)
            except Resolver404:
                landing.nav_is_invalid = True
                landings.append(landing)
                continue
            view_func = match.func
            required_features_any = getattr(
                view_func, "required_features_any", frozenset()
            )
            if required_features_any:
                if not any(feature_is_enabled(slug) for slug in required_features_any):
                    landing.nav_is_invalid = True
                    landings.append(landing)
                    continue
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
            landing.nav_is_invalid = landing.nav_is_invalid or (
                not landing.is_link_valid()
            )
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
            module.enabled_landings_all_invalid = all(
                landing.nav_is_invalid for landing in landings
            )
            module.enabled_landings = landings
            valid_modules.append(module)
            if request.path.startswith(module.path):
                if current_module is None or len(module.path) > len(
                    current_module.path
                ):
                    current_module = module


    valid_modules.sort(key=lambda m: (m.priority, m.menu_label.lower()))

    if current_module and current_module.favicon_url:
        favicon_url = current_module.favicon_url
    else:
        favicon_url = None
        if site:
            try:
                if site.badge.favicon_url:
                    favicon_url = site.badge.favicon_url
            except Exception:
                pass
        if not favicon_url:
            role_name = getattr(getattr(node, "role", None), "name", "")
            favicon_url = _ROLE_FAVICONS.get(role_name, _DEFAULT_FAVICON) or _DEFAULT_FAVICON

    try:
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
    except (OperationalError, ProgrammingError):
        header_references = []

    try:
        chat_feature = NodeFeature.objects.filter(slug="chat-bridge").first()
    except (OperationalError, ProgrammingError):
        chat_feature = None
    chat_enabled = bool(
        getattr(settings, "PAGES_CHAT_ENABLED", False)
        and chat_feature
        and chat_feature.is_enabled
    )
    chat_socket_path = getattr(settings, "PAGES_CHAT_SOCKET_PATH", "/ws/pages/chat/")

    site_template = None
    if user_is_authenticated:
        try:
            site_template = getattr(user, "site_template", None)
        except Exception:
            site_template = None
        if site_template is None:
            try:
                group_template = (
                    SecurityGroup.objects.filter(
                        site_template__isnull=False, user=user
                    )
                    .select_related("site_template")
                    .order_by("name")
                    .first()
                )
            except (OperationalError, ProgrammingError):
                group_template = None
            else:
                if group_template:
                    site_template = group_template.site_template

    if site_template is None and site:
        site_template = getattr(site, "template", None)
    if site_template is None:
        try:
            site_template = SiteTemplate.objects.order_by("name").first()
        except (OperationalError, ProgrammingError):
            site_template = None

    return {
        "nav_modules": valid_modules,
        "favicon_url": favicon_url,
        "header_references": header_references,
        "login_url": resolve_url(settings.LOGIN_URL),
        "chat_enabled": chat_enabled,
        "chat_socket_path": chat_socket_path,
        "site_template": site_template,
    }
