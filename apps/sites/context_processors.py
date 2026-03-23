from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import resolve_url
from django.urls import Resolver404, resolve
from django.utils.encoding import force_str

from apps.features.utils import (
    is_pages_chat_runtime_enabled,
    is_suite_feature_enabled,
)
from apps.groups.models import SecurityGroup
from apps.links.models import Reference
from apps.links.reference_utils import filter_visible_references
from apps.modules.models import Module
from apps.nodes.models import Node
from apps.nodes.utils import FeatureChecker
from apps.sites.utils import user_in_site_operator_group
from utils.sites import get_site

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
    """Return the base64 favicon payload for ``filename`` when available."""

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


def _parse_user_story_attachment_limit() -> int:
    """Return the configured attachment limit with a safe integer fallback.

    Returns:
        int: Parsed attachment limit, or ``3`` when the setting is invalid.
    """

    raw_limit = getattr(settings, "USER_STORY_ATTACHMENT_LIMIT", 3)
    try:
        return int(raw_limit)
    except (TypeError, ValueError):
        return 3


def _resolve_landing_visibility(
    landing,
    view_func,
    request,
    *,
    role_id: object,
    site_id: object,
    user_cache_key: object,
) -> bool:
    """Return whether a landing should be visible in module navigation.

    Parameters:
        landing: Landing being considered for display.
        view_func: Resolved view callable for the landing.
        request: Current Django request.
        role_id: Current node role identifier for cache scoping.
        site_id: Current site identifier for cache scoping.
        user_cache_key: Stable per-user cache scope for user-sensitive validators.

    Returns:
        bool: Whether the landing should remain visible in navigation.
    """

    validator = getattr(view_func, "module_pill_link_validator", None)
    if validator is None:
        return True

    parameter_getter = getattr(
        view_func,
        "module_pill_link_validator_parameter_getter",
        None,
    )
    parameters: dict[str, object] = {}
    if callable(parameter_getter):
        values = parameter_getter(request=request, landing=landing)
        if values:
            parameters = dict(values)

    ttl_from_attr = getattr(view_func, "module_pill_link_validator_cache_ttl", 60)
    cache_ttl = int(ttl_from_attr if ttl_from_attr is not None else 60)
    params_fingerprint = "|".join(
        f"{key}={force_str(parameters[key])}" for key in sorted(parameters)
    )
    cache_key = (
        "nav_links:landing_visibility:"
        f"{landing.path}:"
        f"role:{role_id}:"
        f"site:{site_id}:"
        f"user:{user_cache_key}:"
        f"params:{params_fingerprint}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return bool(cached)

    is_visible = bool(validator(request=request, landing=landing, **parameters))
    cache.set(cache_key, is_visible, timeout=cache_ttl)
    return is_visible


def _initialize_request_badges(request):
    """Populate request badge fields with database-safe fallbacks."""

    site = getattr(request, "badge_site", None) or get_site(request)
    node = getattr(request, "badge_node", None)
    role = getattr(request, "badge_role", None)
    try:
        if node is None:
            node = Node.get_local()
        if role is None:
            role = node.role if node else None
    except (OperationalError, ProgrammingError):
        pass
    request.badge_site = site
    request.badge_node = node
    request.badge_role = role
    return site, node, role


def _get_user_group_membership(user) -> tuple[set[str], set[int]]:
    """Return the user's security-group names and ids when available."""

    if (
        not getattr(user, "is_authenticated", False)
        or getattr(user, "pk", None) is None
    ):
        return set(), set()
    return (
        set(user.groups.values_list("name", flat=True)),
        set(user.groups.values_list("id", flat=True)),
    )


def _module_matches_navigation_access(
    module, role, user, user_group_ids: set[int]
) -> bool:
    """Return whether ``module`` is eligible for the current role and groups."""

    module_roles = getattr(module, "roles")
    role_ids = (
        {candidate.id for candidate in module_roles.all()} if module_roles else set()
    )
    role_matches = not role_ids or (role and role.id in role_ids)
    group_matches = bool(
        module.security_group_id
        and getattr(user, "is_authenticated", False)
        and module.security_group_id in user_group_ids
    )
    if module.security_group_id:
        if module.security_mode == Module.SECURITY_EXCLUSIVE:
            return bool(role_matches and group_matches)
        return bool(role_matches or group_matches)
    return bool(role_matches)


def _load_visible_modules(
    role, user, feature_checker, user_group_ids: set[int]
) -> list[Module]:
    """Load modules that survive feature and access checks for navigation.

    Parameters:
        role: Active node role used by the module manager.
        user: Current request user.
        feature_checker: FeatureChecker used for module feature gates.
        user_group_ids: Current user's security group ids.

    Returns:
        list[Module]: Modules eligible for further landing annotation.
    """

    try:
        modules = (
            Module.objects.for_role(role)
            .filter(is_deleted=False)
            .select_related("application", "security_group")
            .prefetch_related("landings", "roles", "features")
        )
    except (OperationalError, ProgrammingError):
        return []

    try:
        candidate_modules = list(modules)
    except (OperationalError, ProgrammingError):
        return []

    visible_modules: list[Module] = []
    for module in candidate_modules:
        if not module.meets_feature_requirements(feature_checker.is_enabled):
            continue
        if not _module_matches_navigation_access(module, role, user, user_group_ids):
            continue
        visible_modules.append(module)
    return visible_modules


def _compute_landing_lock_state(
    landing, view_func, request, user_group_names: set[str]
):
    """Return lock metadata for a landing based on auth, staff, and group rules."""

    user = getattr(request, "user", None)
    user_is_authenticated = getattr(user, "is_authenticated", False)
    user_is_superuser = getattr(user, "is_superuser", False)

    requires_login = bool(getattr(view_func, "login_required", False))
    if not requires_login and hasattr(view_func, "login_url"):
        requires_login = True
    staff_only = getattr(view_func, "staff_required", False)
    required_groups = getattr(view_func, "required_security_groups", frozenset())

    blocked_reason = None
    if required_groups:
        requires_login = True
        if not user_is_authenticated:
            blocked_reason = "login"
        elif not user_is_superuser and not (user_group_names & set(required_groups)):
            blocked_reason = "permission"
    elif requires_login and not user_is_authenticated:
        blocked_reason = "login"

    if (
        staff_only
        and not getattr(user, "is_staff", False)
        and blocked_reason != "login"
    ):
        blocked_reason = "permission"

    return {
        "nav_is_locked": bool(blocked_reason),
        "nav_lock_reason": blocked_reason,
    }


def _select_primary_landings(module_path: str, landings: list):
    """Return the preferred visible landings for a module path.

    Parameters:
        module_path: Module base path.
        landings: Visible landing objects for the module.

    Returns:
        list: The preferred landing list, preserving historical `/read` behavior.
    """

    normalized_module_path = module_path.rstrip("/") or "/"
    if landings and normalized_module_path == "/read":
        primary_landings = [
            landing
            for landing in landings
            if landing.path.rstrip("/") == normalized_module_path
        ]
        return primary_landings or [landings[0]]
    return landings


def _assign_module_menu(module):
    """Normalize special module menu labels in place.

    Parameters:
        module: Module instance being prepared for navigation.

    Returns:
        Module: The same module with any special menu label adjustments applied.
    """

    app_name = getattr(module.application, "name", "").lower()
    if app_name == "awg":
        module.menu = "Calculators"
    elif module.path.rstrip("/").lower() == "/man":
        module.menu = "Manual"
    return module


def _annotate_module_landings(
    module,
    request,
    *,
    feature_checker,
    role_id: object,
    site_id: object,
    user_cache_key: object,
    user_group_names: set[str],
):
    """Attach visible landing metadata to ``module`` for navigation rendering."""

    landings = []
    seen_paths: set[str] = set()
    for landing in module.landings.filter(enabled=True):
        normalized_path = landing.path.rstrip("/") or "/"
        if normalized_path in seen_paths:
            continue

        landing.nav_is_invalid = not landing.is_link_valid()
        landing.nav_is_locked = False
        landing.nav_lock_reason = None
        try:
            match = resolve(landing.path)
        except Resolver404:
            landing.nav_is_invalid = True
            seen_paths.add(normalized_path)
            landings.append(landing)
            continue

        view_func = match.func
        required_features_any = getattr(view_func, "required_features_any", frozenset())
        if required_features_any and not any(
            feature_checker.is_enabled(slug) for slug in required_features_any
        ):
            continue
        if not _resolve_landing_visibility(
            landing,
            view_func,
            request,
            role_id=role_id,
            site_id=site_id,
            user_cache_key=user_cache_key,
        ):
            continue

        seen_paths.add(normalized_path)
        for key, value in _compute_landing_lock_state(
            landing,
            view_func,
            request,
            user_group_names=user_group_names,
        ).items():
            setattr(landing, key, value)
        landings.append(landing)

    landings = _select_primary_landings(module.path, landings)

    if not landings:
        return None

    _assign_module_menu(module)
    module.enabled_landings_all_invalid = all(
        landing.nav_is_invalid for landing in landings
    )
    module.enabled_landings = landings
    return module


def _select_current_module(request_path: str, modules: list[Module]):
    """Return the most specific visible module for ``request_path``."""

    current_module = None
    for module in modules:
        if request_path.startswith(module.path):
            if current_module is None or len(module.path) > len(current_module.path):
                current_module = module
    return current_module


def _select_favicon_url(current_module, site, node) -> str | None:
    """Return the favicon URL or inline payload for the current request."""

    if current_module and current_module.favicon_url:
        return current_module.favicon_url

    favicon_url = None
    if site:
        try:
            badge = site.badge
            if badge.favicon_url:
                favicon_url = badge.favicon_url
        except Exception:
            favicon_url = None
    if favicon_url:
        return favicon_url

    role_name = getattr(getattr(node, "role", None), "name", "")
    return _ROLE_FAVICONS.get(role_name, _DEFAULT_FAVICON) or _DEFAULT_FAVICON


def _load_header_references(request, site, node):
    """Return header references that are visible in the current request context."""

    try:
        header_refs_qs = (
            Reference.objects.filter(show_in_header=True)
            .exclude(value="")
            .prefetch_related("roles", "features", "sites")
        )
        return filter_visible_references(
            header_refs_qs,
            request=request,
            site=site,
            node=node,
        )
    except (OperationalError, ProgrammingError):
        return []


def _build_chat_context(site, user, *, pages_chat_enabled: bool):
    """Return chat-related context flags for the current request.

    Parameters:
        site: Current Django site-like object.
        user: Current request user.
        pages_chat_enabled: Deployment/runtime prerequisite for pages chat.

    Returns:
        dict[str, object]: Chat widget flags for public and staff templates.
    """

    user_is_authenticated = getattr(user, "is_authenticated", False)
    user_has_pk = getattr(user, "pk", None) is not None
    user_is_staff = getattr(user, "is_staff", False)
    user_is_superuser = getattr(user, "is_superuser", False)
    staff_chat_bridge_enabled = is_suite_feature_enabled(
        "staff-chat-bridge", default=False
    )
    site_public_chat_enabled = bool(getattr(site, "enable_public_chat", False))
    user_chat_opt_in = False
    if user_is_authenticated and user_has_pk:
        try:
            profile = user.get_profile(apps.get_model("users", "ChatProfile"))
        except (LookupError, ObjectDoesNotExist, AttributeError):
            profile = None
        user_chat_opt_in = bool(profile and profile.contact_via_chat)

    pages_chat_runtime_enabled = bool(pages_chat_enabled)
    public_chat_enabled = bool(
        pages_chat_runtime_enabled and (site_public_chat_enabled or user_chat_opt_in)
    )
    staff_chat_bridge_allowed = bool(
        pages_chat_runtime_enabled
        and staff_chat_bridge_enabled
        and user_is_authenticated
        and (user_is_staff or user_is_superuser)
    )
    return {
        "chat_enabled": public_chat_enabled or staff_chat_bridge_allowed,
        "chat_opt_in_checked": user_chat_opt_in,
        "chat_socket_path": getattr(
            settings, "PAGES_CHAT_SOCKET_PATH", "/ws/pages/chat/"
        ),
    }


def _get_user_group_site_template(user):
    """Return the first security-group site template available to ``user``.

    Parameters:
        user: Current request user.

    Returns:
        SiteTemplate | None: The first matching group template, if any.
    """

    try:
        group_template = (
            SecurityGroup.objects.filter(site_template__isnull=False, user=user)
            .select_related("site_template")
            .order_by("name")
            .first()
        )
    except (OperationalError, ProgrammingError):
        return None
    return group_template.site_template if group_template else None


def _select_site_template(site, user):
    """Return the best site template for the current user and site."""

    site_template = None
    if (
        getattr(user, "is_authenticated", False)
        and getattr(user, "pk", None) is not None
    ):
        try:
            site_template = getattr(user, "site_template", None)
        except (AttributeError, ObjectDoesNotExist, OperationalError, ProgrammingError):
            site_template = None
        if site_template is None:
            site_template = _get_user_group_site_template(user)

    if site_template is None and site:
        site_template = getattr(site, "template", None)
    if site_template is not None:
        return site_template
    try:
        return SiteTemplate.objects.order_by("name").first()
    except (OperationalError, ProgrammingError):
        return None


def nav_links(request):
    """Provide navigation links and related site chrome for the current request."""

    site, node, role = _initialize_request_badges(request)
    user = getattr(request, "user", None)
    user_is_authenticated = getattr(user, "is_authenticated", False)
    user_is_staff = getattr(user, "is_staff", False)
    user_is_superuser = getattr(user, "is_superuser", False)
    is_site_operator = user_in_site_operator_group(user)
    role_id = getattr(role, "id", "none")
    site_id = getattr(site, "id", "none")
    operator_interface_requested = request.GET.get("operator_interface") in {
        "1",
        "true",
        "True",
    }
    operator_interface_mode = (
        operator_interface_requested
        and user_is_authenticated
        and (user_is_staff or user_is_superuser or is_site_operator)
        and not is_suite_feature_enabled("operator-site-interface", default=True)
    )
    feedback_ingestion_enabled = is_suite_feature_enabled(
        "feedback-ingestion", default=True
    )
    pages_chat_enabled = is_pages_chat_runtime_enabled(default=False)
    feature_checker = FeatureChecker()
    user_group_names, user_group_ids = _get_user_group_membership(user)
    user_cache_key = getattr(user, "pk", None) if user_is_authenticated else "anonymous"

    candidate_modules = _load_visible_modules(
        role, user, feature_checker, user_group_ids
    )
    annotated_modules = [
        annotated_module
        for module in candidate_modules
        if (
            annotated_module := _annotate_module_landings(
                module,
                request,
                feature_checker=feature_checker,
                role_id=role_id,
                site_id=site_id,
                user_cache_key=user_cache_key,
                user_group_names=user_group_names,
            )
        )
        is not None
    ]
    annotated_modules.sort(
        key=lambda module: (module.priority, module.menu_label.lower())
    )
    current_module = _select_current_module(request.path, annotated_modules)
    request.current_module = current_module

    context = {
        "nav_modules": annotated_modules,
        "current_module": current_module,
        "favicon_url": _select_favicon_url(current_module, site, node),
        "header_references": _load_header_references(request, site, node),
        "login_url": resolve_url(settings.LOGIN_URL),
        "site_template": _select_site_template(site, user),
        "operator_interface_mode": operator_interface_mode,
        "feedback_ingestion_enabled": feedback_ingestion_enabled,
        "user_story_attachment_limit": _parse_user_story_attachment_limit(),
    }
    context.update(
        _build_chat_context(
            site,
            user,
            pages_chat_enabled=pages_chat_enabled,
        )
    )
    return context
