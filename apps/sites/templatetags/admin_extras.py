import ast
import inspect
import logging
import textwrap
import weakref
from pathlib import Path
from typing import Any, Iterable

from django import template
from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.contrib.staticfiles import finders
from django.db import DatabaseError
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models import Model
from django.templatetags.static import static
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html_join
from django.utils.text import capfirst
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.actions.models import DashboardAction
from apps.celery.utils import celery_feature_enabled as celery_feature_enabled_helper
from apps.core.entity import Entity
from apps.nodes.models import NetMessage, Node
from apps.counters.dashboard_rules import DEFAULT_SUCCESS_MESSAGE
from apps.counters.models import DashboardRule

register = template.Library()

logger = logging.getLogger(__name__)
_USES_QUERYSET_CACHE = weakref.WeakKeyDictionary()


def _content_type_for_model(model: type[Model]) -> ContentType | None:
    """Return content type for a model using batched lookup APIs."""

    return ContentType.objects.get_for_models(
        model, for_concrete_models=False
    ).get(model)


def _resolve_current_admin_app_label(request) -> str:
    """Return the active admin app label inferred from request resolver metadata."""

    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match is None:
        return ""

    app_label = resolver_match.kwargs.get("app_label")
    if isinstance(app_label, str) and app_label:
        return app_label

    url_name = resolver_match.url_name or ""
    if "_" not in url_name:
        return ""

    candidate_label = url_name.split("_", 1)[0]
    if not candidate_label:
        return ""

    try:
        apps.get_app_config(candidate_label)
    except LookupError:
        return ""
    return candidate_label


def _stylesheet_exists(stylesheet_path: str) -> bool:
    """Return whether a stylesheet path can be located via staticfiles finders."""

    if not stylesheet_path:
        return False
    return bool(finders.find(stylesheet_path))


def _configured_admin_stylesheets(app_label: str) -> list[str]:
    """Return framework/global/app stylesheet paths for the active admin context."""

    stylesheet_paths: list[str] = []

    configured_base = getattr(settings, "ADMIN_BASE_STYLESHEET", "")
    if configured_base:
        stylesheet_paths.append(configured_base)

    for stylesheet in getattr(settings, "ADMIN_GLOBAL_STYLESHEETS", []):
        if stylesheet and stylesheet not in stylesheet_paths:
            stylesheet_paths.append(stylesheet)

    app_stylesheets = getattr(settings, "ADMIN_APP_STYLESHEETS", {})
    configured_app_stylesheet = app_stylesheets.get(app_label, "") if app_label else ""
    if configured_app_stylesheet and configured_app_stylesheet not in stylesheet_paths:
        stylesheet_paths.append(configured_app_stylesheet)

    inferred_app_stylesheet = f"{app_label}/css/admin.css" if app_label else ""
    if (
        inferred_app_stylesheet
        and inferred_app_stylesheet not in stylesheet_paths
        and _stylesheet_exists(inferred_app_stylesheet)
    ):
        stylesheet_paths.append(inferred_app_stylesheet)

    return stylesheet_paths


@register.simple_tag(takes_context=True)
def render_admin_stylesheets(context) -> str:
    """Render stylesheet links for base, global, and active app-specific admin CSS."""

    request = context.get("request")
    active_app_label = _resolve_current_admin_app_label(request)
    stylesheet_paths = _configured_admin_stylesheets(active_app_label)
    return format_html_join(
        "\n",
        '<link rel="stylesheet" href="{}">',
        ((static(stylesheet_path),) for stylesheet_path in stylesheet_paths),
    )


@register.simple_tag
def safe_admin_url(view_name: str, *args, **kwargs) -> str:
    """Reverse an admin URL and gracefully handle missing patterns."""

    try:
        return reverse(view_name, args=args, kwargs=kwargs)
    except NoReverseMatch:
        return ""


def _admin_model_instance(model_admin, request, user):
    model = model_admin.model
    if isinstance(user, model):
        return user
    try:
        queryset = model_admin.get_queryset(request)
    except Exception:
        queryset = model._default_manager.all()
    try:
        return queryset.get(pk=user.pk)
    except model.DoesNotExist:
        return None


def _admin_has_access(model_admin, request, obj):
    if hasattr(model_admin, "has_view_or_change_permission"):
        if model_admin.has_view_or_change_permission(request, obj=obj):
            return True
    else:
        has_change = getattr(model_admin, "has_change_permission", None)
        if has_change and has_change(request, obj):
            return True
        has_view = getattr(model_admin, "has_view_permission", None)
        if has_view and has_view(request, obj):
            return True
    return False


def _admin_change_url(model, user):
    opts = model._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[user.pk])


@register.simple_tag(takes_context=True)
def admin_profile_url(context, user) -> str:
    """Return the first accessible admin change URL for the given user."""

    request = context.get("request")
    if request is None or user is None or not getattr(user, "pk", None):
        return ""

    def model_profile_url(model):
        model_admin = admin.site._registry.get(model)
        if not model_admin:
            return ""

        obj = _admin_model_instance(model_admin, request, user)
        if obj is None:
            return ""

        if not _admin_has_access(model_admin, request, obj):
            return ""

        try:
            return _admin_change_url(model_admin.model, user)
        except NoReverseMatch:
            return ""

    teams_user = None
    try:
        teams_user = apps.get_model("teams", "User")
    except LookupError:
        pass

    if teams_user and teams_user in admin.site._registry:
        return model_profile_url(teams_user)

    candidate_models = (
        ("core", "User"),
        ("auth", "User"),
    )

    for app_label, model_name in candidate_models:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue

        url = model_profile_url(model)
        if url:
            return url

    return ""


@register.simple_tag
def last_net_message() -> dict[str, object]:
    """Return the most recent NetMessage with content for the admin dashboard."""

    try:
        now = timezone.now()
        entries = list(
            NetMessage.objects.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .order_by("-created")
            .values("pk", "subject", "body")[:25]
        )
    except DatabaseError:
        return {"text": "", "has_content": False, "pk": None, "url": ""}

    for entry in entries:
        subject = (entry.get("subject") or "").strip()
        body = (entry.get("body") or "").strip()
        parts = [part for part in (subject, body) if part]
        if parts:
            text = " — ".join(parts)
            pk = entry.get("pk")
            url = ""
            if pk:
                try:
                    url = reverse("admin:nodes_netmessage_change", args=[pk])
                except NoReverseMatch:
                    pass

            return {"text": text, "has_content": True, "pk": pk, "url": url}

    return {"text": "", "has_content": False, "pk": None, "url": ""}


@register.simple_tag
def admin_translate_url(language_tabs) -> str:
    """Return the first available translation URL for parler language tabs."""

    if not language_tabs:
        return ""

    for url, _name, _code, status in language_tabs:
        if url:
            return url

    return ""


def _model_admin_action_key(value: str) -> str:
    """Return a normalized identifier used to deduplicate admin actions."""

    return str(value or "").strip().lower().replace("-", "_")

def _model_admin_action_uses_queryset(func) -> bool:
    """Return whether an admin action implementation references ``queryset``."""

    func = inspect.unwrap(func)
    cached = _USES_QUERYSET_CACHE.get(func)
    if cached is not None:
        return cached
    try:
        source = textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError):
        _USES_QUERYSET_CACHE[func] = True
        return True
    try:
        tree = ast.parse(source)
    except SyntaxError:
        _USES_QUERYSET_CACHE[func] = True
        return True
    func_node = next(
        (
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
        None,
    )
    if func_node is None:
        _USES_QUERYSET_CACHE[func] = True
        return True

    class Finder(ast.NodeVisitor):
        def __init__(self):
            self.found = False

        def visit_Name(self, node):
            if node.id == "queryset":
                self.found = True

    finder = Finder()
    for node in func_node.body:
        if finder.found:
            break
        finder.visit(node)
    _USES_QUERYSET_CACHE[func] = finder.found
    return finder.found

def _model_admin_action_capabilities(action_name, func, *, skip_queryset_actions):
    """Describe the static capabilities of an admin action implementation.

    Parameters:
        action_name: Action identifier reported by Django admin.
        func: Callable that implements the action.
        skip_queryset_actions: Whether queryset-backed actions should be hidden.

    Returns:
        Dict containing normalized metadata used during visibility checks.
    """

    requires_queryset = getattr(func, "requires_queryset", None)
    if requires_queryset is None:
        requires_queryset = _model_admin_action_uses_queryset(func)
    return {
        "action_name": action_name,
        "action_key": _model_admin_action_key(action_name),
        "is_delete_selected": action_name == "delete_selected",
        "requires_queryset": bool(requires_queryset),
        "skip_queryset_actions": bool(skip_queryset_actions),
        "is_discover": bool(getattr(func, "is_discover_action", False)),
    }

def _model_admin_action_eligibility(capabilities, seen_actions):
    """Return structured visibility state for a candidate admin action.

    Parameters:
        capabilities: Static action capability metadata.
        seen_actions: Set of normalized action keys already emitted.

    Returns:
        Dict describing whether the action should be rendered and why.
    """

    action_key = capabilities["action_key"]
    is_duplicate = action_key in seen_actions
    hidden_by_queryset = capabilities["skip_queryset_actions"] and capabilities["requires_queryset"]
    is_visible = not (
        capabilities["is_delete_selected"] or is_duplicate or hidden_by_queryset
    )
    return {
        "action_name": capabilities["action_name"],
        "action_key": action_key,
        "is_visible": is_visible,
        "is_duplicate": is_duplicate,
        "hidden_by_queryset": hidden_by_queryset,
        "is_delete_selected": capabilities["is_delete_selected"],
        "is_discover": capabilities["is_discover"],
    }

def _model_admin_action_label(model_admin, action_name, func, default_label, request):
    """Return the display label for a legacy admin action."""

    label = getattr(func, "label", default_label)
    if action_name != "my_profile":
        return label

    label_getter = getattr(model_admin, "get_my_profile_label", None)
    if not callable(label_getter):
        return label
    try:
        dynamic_label = label_getter(request)
    except Exception:  # pragma: no cover - defensive fallback
        return label
    return dynamic_label or label

def _model_admin_action_base_view_name(model_admin) -> str:
    """Return the shared admin view-name prefix for a model admin."""

    return f"admin:{model_admin.opts.app_label}_{model_admin.opts.model_name}_"

def _model_admin_action_default_url(model_admin, action_name):
    """Return the fallback URL used by legacy admin and changelist actions."""

    base = _model_admin_action_base_view_name(model_admin)
    try:
        return reverse(base + action_name)
    except NoReverseMatch:
        try:
            return reverse(base + action_name.split("_")[0])
        except NoReverseMatch:
            return reverse(base + "changelist") + f"?action={action_name}"

def _model_admin_action_tools_url(model_admin, action_name):
    """Return a django-object-actions tool URL when configured."""

    tools_view_name = getattr(model_admin, "tools_view_name", None)
    if not tools_view_name:
        initializer = getattr(model_admin, "_get_action_urls", None)
        if callable(initializer):
            try:
                initializer()
            except Exception:  # pragma: no cover - defensive
                tools_view_name = None
            else:
                tools_view_name = getattr(model_admin, "tools_view_name", None)
    if not tools_view_name:
        return ""
    try:
        return reverse(tools_view_name, kwargs={"tool": action_name})
    except NoReverseMatch:
        return ""

def _model_admin_action_url(model_admin, action_name, func, request, source):
    """Return the rendered URL for a legacy admin action source."""

    if source == "dashboard":
        dashboard_url = getattr(func, "dashboard_url", None)
        if not isinstance(dashboard_url, str):
            return ""
        try:
            return reverse(dashboard_url)
        except NoReverseMatch:
            return dashboard_url

    if action_name == "my_profile":
        getter = getattr(model_admin, "get_my_profile_url", None)
        if callable(getter):
            url = getter(request)
            if url:
                return url

    if source == "changelist":
        tools_url = _model_admin_action_tools_url(model_admin, action_name)
        if tools_url:
            return tools_url

    return _model_admin_action_default_url(model_admin, action_name)

def _configured_dashboard_action_descriptors(model):
    """Return normalized descriptors for configured dashboard actions."""

    content_type = _content_type_for_model(model)
    if content_type is None:
        return []

    descriptors = []
    for configured_action in DashboardAction.objects.filter(
        content_type=content_type,
        is_active=True,
    ).select_related("recipe"):
        descriptors.append(
            {
                "action_key": _model_admin_action_key(configured_action.slug),
                "label": configured_action.label,
                "url": configured_action.resolve_url(),
                "method": configured_action.http_method,
                "caller_sigil": configured_action.caller_sigil,
                "is_discover": configured_action.label.strip().lower() == "discover",
            }
        )
    return descriptors

def _model_admin_named_actions(model_admin, getter_name, request):
    """Return action names exposed by an optional model-admin getter."""

    getter = getattr(model_admin, getter_name, None)
    if not callable(getter):
        return []
    try:
        action_names = getter(request)
    except TypeError:
        action_names = getter()
    return action_names or []

def _build_model_admin_action_descriptor(
    model_admin,
    action_name,
    func,
    *,
    default_label,
    request,
    source,
    seen_actions,
    skip_queryset_actions,
):
    """Return a normalized descriptor for a visible legacy admin action."""

    capabilities = _model_admin_action_capabilities(
        action_name,
        func,
        skip_queryset_actions=skip_queryset_actions,
    )
    eligibility = _model_admin_action_eligibility(capabilities, seen_actions)
    if not eligibility["is_visible"]:
        return None

    descriptor = {
        "action_key": eligibility["action_key"],
        "label": _model_admin_action_label(
            model_admin,
            action_name,
            func,
            default_label,
            request,
        ),
        "url": _model_admin_action_url(model_admin, action_name, func, request, source),
        "method": getattr(func, "dashboard_method", "get") if source == "dashboard" else "get",
        "caller_sigil": "",
        "is_discover": eligibility["is_discover"],
    }
    if not descriptor["url"]:
        return None
    return descriptor

def _normalized_model_admin_actions(request, model_admin):
    """Return normalized descriptors for configured and legacy admin actions."""

    seen_actions = set()
    descriptors = []

    for descriptor in _configured_dashboard_action_descriptors(model_admin.model):
        if not descriptor["url"]:
            continue
        descriptors.append(descriptor)
        seen_actions.add(descriptor["action_key"])

    for action_name, (func, _name, description) in model_admin.get_actions(request).items():
        descriptor = _build_model_admin_action_descriptor(
            model_admin,
            action_name,
            func,
            default_label=description or action_name.replace("_", " "),
            request=request,
            source="actions",
            seen_actions=seen_actions,
            skip_queryset_actions=True,
        )
        if descriptor is None:
            continue
        descriptors.append(descriptor)
        seen_actions.add(descriptor["action_key"])

    for action_name in _model_admin_named_actions(model_admin, "get_changelist_actions", request):
        func = getattr(model_admin, action_name, None)
        if func is None:
            continue
        descriptor = _build_model_admin_action_descriptor(
            model_admin,
            action_name,
            func,
            default_label=getattr(func, "short_description", action_name.replace("_", " ")),
            request=request,
            source="changelist",
            seen_actions=seen_actions,
            skip_queryset_actions=True,
        )
        if descriptor is None:
            continue
        descriptors.append(descriptor)
        seen_actions.add(descriptor["action_key"])

    for action_name in _model_admin_named_actions(model_admin, "get_dashboard_actions", request):
        func = getattr(model_admin, action_name, None)
        if func is None:
            continue
        descriptor = _build_model_admin_action_descriptor(
            model_admin,
            action_name,
            func,
            default_label=getattr(func, "short_description", action_name.replace("_", " ")),
            request=request,
            source="dashboard",
            seen_actions=seen_actions,
            skip_queryset_actions=False,
        )
        if descriptor is None:
            continue
        descriptors.append(descriptor)
        seen_actions.add(descriptor["action_key"])

    return descriptors

def _format_model_admin_action(descriptor):
    """Return the template payload for a normalized admin action descriptor."""

    action = DashboardAction.from_legacy(
        label=str(descriptor["label"]),
        method=str(descriptor["method"]),
        url=descriptor["url"],
        caller_sigil=str(descriptor["caller_sigil"]),
    ).as_rendered_action()
    action["is_discover"] = bool(descriptor["is_discover"]) or bool(action["is_discover"])
    return action


@register.simple_tag(takes_context=True)
def model_admin_actions(context, app_label, model_name):
    """Return available admin actions for the given model."""

    request = context.get("request")
    cache_key = (app_label, model_name)
    if request is not None:
        try:
            action_cache = request._dashboard_action_cache
        except AttributeError:
            action_cache = request._dashboard_action_cache = {}
        cached_actions = action_cache.get(cache_key)
        if cached_actions is not None:
            return cached_actions
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return []
    model_admin = admin.site._registry.get(model)
    if not model_admin:
        return []

    actions = [
        _format_model_admin_action(descriptor)
        for descriptor in _normalized_model_admin_actions(request, model_admin)
    ]
    if request is not None:
        action_cache[cache_key] = actions
    return actions


@register.simple_tag
def admin_changelist_url(ct: ContentType) -> str:
    """Return the admin changelist URL for the given content type."""
    try:
        return reverse(f"admin:{ct.app_label}_{ct.model}_changelist")
    except NoReverseMatch:
        return ""


@register.simple_tag
def dashboard_model_status(app_label: str, model_name: str) -> dict | None:
    """Return dashboard rule status for a model when configured."""

    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None

    content_type = _content_type_for_model(model)
    if content_type is None:
        return None

    try:
        rule = DashboardRule.objects.select_related("content_type").get(
            content_type=content_type
        )
    except DashboardRule.DoesNotExist:
        return None

    try:
        status = DashboardRule.get_cached_value(content_type, rule.evaluate)
        if isinstance(status, dict) and status.get("success") and "is_default_message" not in status:
            status["is_default_message"] = status.get("message") == str(
                DEFAULT_SUCCESS_MESSAGE
            )
        return status
    except Exception:
        logger.exception("Unable to evaluate dashboard rule for %s", content_type)
        return None


def _iter_model_classes(
    app_list_to_scan: list[Any],
) -> Iterable[type[Model]]:
    """Yield model classes from a Django admin app_list."""

    if not app_list_to_scan:
        return

    for app in app_list_to_scan:
        if isinstance(app, dict):
            app_label = app.get("app_label")
            models = app.get("models", [])
        else:
            app_label = getattr(app, "app_label", None)
            models = getattr(app, "models", None)

        if not models:
            continue

        for model in models:
            if isinstance(model, dict):
                model_class = model.get("model")
                model_app_label = model.get("app_label")
                object_name = model.get("object_name")
            else:
                model_class = getattr(model, "model", None)
                model_app_label = getattr(model, "app_label", None)
                object_name = getattr(model, "object_name", None)

            resolved_app_label = model_app_label or app_label
            if model_class is None and resolved_app_label and object_name:
                try:
                    model_class = apps.get_model(resolved_app_label, object_name)
                except LookupError:
                    model_class = None

            if model_class is not None:
                yield model_class


@register.simple_tag
def dashboard_model_status_map(app_list: list[Any]) -> dict[int, dict]:
    """Return dashboard rule status for models in the admin app list."""

    model_classes = list(_iter_model_classes(app_list))

    if not model_classes:
        return {}

    content_type_map = ContentType.objects.get_for_models(
        *model_classes, for_concrete_models=False
    )
    content_types = list(content_type_map.values())
    if not content_types:
        return {}

    rules = DashboardRule.objects.select_related("content_type").filter(
        content_type__in=content_types
    )
    status_map = {}
    for rule in rules:
        content_type = rule.content_type
        try:
            status = DashboardRule.get_cached_value(content_type, rule.evaluate)
            if isinstance(status, dict) and status.get("success") and "is_default_message" not in status:
                status["is_default_message"] = status.get("message") == str(
                    DEFAULT_SUCCESS_MESSAGE
                )
            status_map[content_type.id] = status
        except Exception:
            logger.exception(
                "Unable to evaluate dashboard rule for %s", content_type
            )

    return status_map


@register.filter
def get_status(
    status_map: dict[int, dict],
    content_type_id: int | None,
) -> dict | None:
    """Return a cached dashboard status dict from a status map."""

    if not status_map or not content_type_id:
        return None
    return status_map.get(content_type_id)


@register.simple_tag
def optional_url(viewname: str, *args, **kwargs) -> str:
    """Return ``reverse(viewname)`` or an empty string when missing."""

    try:
        return reverse(viewname, args=args or None, kwargs=kwargs or None)
    except NoReverseMatch:
        return ""


@register.simple_tag
def related_admin_models(opts):
    """Return changelist links for models related to the current model."""

    if not opts:
        return []

    model = getattr(opts, "model", None)
    if model is None:
        return []

    registry = admin.site._registry
    seen = set()
    related = []

    current_labels = {opts.label_lower, model._meta.label_lower}
    concrete = getattr(model._meta, "concrete_model", None)
    if concrete is not None:
        current_labels.add(concrete._meta.label_lower)

    def get_registered(model_cls):
        if not isinstance(model_cls, type) or not issubclass(model_cls, Model):
            return None
        if model_cls in registry:
            return model_cls
        concrete_options = getattr(model_cls, "_meta", None)
        if concrete_options is None:
            return None
        concrete_model = concrete_options.concrete_model
        if concrete_model in registry:
            return concrete_model

        for registered_model in registry:
            registered_options = getattr(registered_model, "_meta", None)
            if registered_options is None:
                continue
            if registered_options.concrete_model == concrete_model:
                return registered_model
        return None

    def describe_relation(field):
        if getattr(field, "one_to_one", False):
            return "1:1", _("One-to-one relationship")
        if getattr(field, "one_to_many", False):
            return "1:N", _("One-to-many relationship")
        if getattr(field, "many_to_one", False):
            return "N:1", _("Many-to-one relationship")
        if getattr(field, "many_to_many", False):
            return "N:N", _("Many-to-many relationship")
        return "—", _("Related model")

    def relation_lookup_name(field):
        if getattr(field, "auto_created", False) and not getattr(field, "concrete", False):
            related_query_name = getattr(field, "related_query_name", None)
            if callable(related_query_name):
                return related_query_name()
            return related_query_name
        return getattr(field, "name", None)

    def target_filter_lookups(target_model_cls):
        lookups = []
        source_labels = set(current_labels)
        source_concrete = getattr(model._meta, "concrete_model", None)
        if source_concrete is not None:
            source_labels.add(source_concrete._meta.label_lower)

        for field in target_model_cls._meta.get_fields(include_hidden=True):
            if not getattr(field, "is_relation", False):
                continue
            related_model = getattr(field, "related_model", None)
            if related_model is None:
                continue

            related_labels = {related_model._meta.label_lower}
            related_concrete = getattr(related_model._meta, "concrete_model", None)
            if related_concrete is not None:
                related_labels.add(related_concrete._meta.label_lower)

            if source_labels.isdisjoint(related_labels):
                continue

            relation_name = relation_lookup_name(field)
            if relation_name:
                lookups.append(f"{relation_name}__id__in")
        resolved = sorted(set(lookups))
        if resolved:
            resolved.append("selected-id__id__in")
        return resolved

    def add_model(model_cls, relation_type: str, relation_title: str):
        registered_model = get_registered(model_cls)
        if registered_model is None:
            return
        model_opts = registered_model._meta
        label_lower = model_opts.label_lower
        if label_lower in current_labels or label_lower in seen:
            return
        concrete_label = model_opts.concrete_model._meta.label_lower
        if concrete_label in current_labels:
            return
        try:
            url = reverse(
                f"admin:{model_opts.app_label}_{model_opts.model_name}_changelist"
            )
        except NoReverseMatch:
            return
        related.append({
            "label": capfirst(model_opts.verbose_name_plural),
            "url": url,
            "relation_type": relation_type,
            "relation_title": relation_title,
            "filter_lookups": target_filter_lookups(registered_model),
            "source_model_label": opts.label_lower,
        })
        seen.add(label_lower)

    for parent in opts.get_parent_list():
        add_model(parent, "1:1", _("Parent model (multi-table inheritance)"))

    for field in opts.get_fields(include_parents=True, include_hidden=True):
        if not getattr(field, "is_relation", False):
            continue
        related_model = getattr(field, "related_model", None)
        if related_model is None:
            continue
        relation_type, relation_title = describe_relation(field)
        add_model(related_model, relation_type, relation_title)

    related.sort(key=lambda item: item["label"])
    return related


@register.simple_tag(takes_context=True)
def celery_feature_enabled(context) -> bool:
    """Return ``True`` when Celery support is enabled for the current node."""

    node = context.get("badge_node")
    return celery_feature_enabled_helper(node)


@register.filter
def supports_user_datum(admin_or_model) -> bool:
    """Return ``True`` when the admin or model supports user datum fixtures."""

    model = getattr(admin_or_model, "model", None) or admin_or_model
    if not isinstance(model, type):
        model = getattr(model, "__class__", None)
    if not isinstance(model, type):
        return False
    if issubclass(model, Entity):
        return True
    return bool(getattr(model, "supports_user_datum", False))


@register.filter
def list_index(sequence, index):
    """Return ``sequence[index]`` while guarding against lookup errors."""

    try:
        position = int(index)
        return sequence[position]
    except (TypeError, ValueError, IndexError):
        return None


@register.simple_tag
def user_data_toggle_url(cl, obj) -> str:
    """Return the admin URL that toggles user datum for ``obj``."""

    if not obj:
        return ""
    try:
        app_label = cl.opts.app_label
        model_name = cl.opts.model_name
    except AttributeError:
        return ""
    try:
        return reverse(
            "admin:user_data_toggle",
            args=(app_label, model_name, obj.pk),
        )
    except NoReverseMatch:
        return ""


@register.simple_tag
def admin_show_filters(cl) -> bool:
    """Return ``True`` when change list filters should be displayed."""

    has_filters = getattr(cl, "has_filters", False)
    if not has_filters:
        return False
    opts = getattr(cl, "opts", None)
    app_label = getattr(opts, "app_label", "") if opts else ""
    model_name = getattr(opts, "model_name", "") if opts else ""
    return not (app_label == "ocpp" and model_name == "charger")
