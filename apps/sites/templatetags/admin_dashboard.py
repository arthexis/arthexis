"""Admin dashboard template tags and helpers."""

import ast
import inspect
import logging
import textwrap
import weakref
from typing import Any, Iterable

from django import template
from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError
from django.db.models import Model, Q
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from apps.counters.models import DashboardRule
from apps.nodes.models import NetMessage

register = template.Library()
logger = logging.getLogger(__name__)
_USES_QUERYSET_CACHE = weakref.WeakKeyDictionary()


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

    def uses_queryset(func):
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

    actions = []
    seen = set()

    def add_action(action_name, func, label, url):
        if not url:
            return
        label_text = str(label)
        actions.append(
            {
                "url": url,
                "label": label,
                "is_discover": getattr(func, "is_discover_action", False)
                or label_text.strip().lower() == "discover",
            }
        )
        seen.add(action_name)

    for action_name, (func, _name, description) in model_admin.get_actions(
        request
    ).items():
        requires_queryset = getattr(func, "requires_queryset", None)
        if action_name == "delete_selected" or requires_queryset is True:
            continue
        if requires_queryset is None and uses_queryset(func):
            continue
        url = None
        label = getattr(
            func,
            "label",
            description or _name.replace("_", " "),
        )
        if action_name == "my_profile":
            getter = getattr(model_admin, "get_my_profile_url", None)
            if callable(getter):
                url = getter(request)
            label_getter = getattr(model_admin, "get_my_profile_label", None)
            if callable(label_getter):
                try:
                    dynamic_label = label_getter(request)
                except Exception:  # pragma: no cover - defensive fallback
                    dynamic_label = None
                if dynamic_label:
                    label = dynamic_label
        base = f"admin:{model_admin.opts.app_label}_{model_admin.opts.model_name}_"
        if not url:
            try:
                url = reverse(base + action_name)
            except NoReverseMatch:
                try:
                    url = reverse(base + action_name.split("_")[0])
                except NoReverseMatch:
                    url = reverse(base + "changelist") + f"?action={action_name}"
        add_action(action_name, func, label, url)

    def get_named_actions(getter_name):
        getter = getattr(model_admin, getter_name, None)
        if not callable(getter):
            return []
        try:
            action_names = getter(request)
        except TypeError:
            action_names = getter()
        return action_names or []

    def iter_model_admin_named_actions(action_names, *, skip_queryset_actions):
        for action_name in action_names:
            if action_name in seen:
                continue
            func = getattr(model_admin, action_name, None)
            if func is None:
                continue
            if skip_queryset_actions:
                requires_queryset = getattr(func, "requires_queryset", None)
                if requires_queryset is True:
                    continue
                if requires_queryset is None and uses_queryset(func):
                    continue
            label = getattr(
                func,
                "label",
                getattr(
                    func,
                    "short_description",
                    action_name.replace("_", " "),
                ),
            )
            yield action_name, func, label

    for action_name, func, label in iter_model_admin_named_actions(
        get_named_actions("get_changelist_actions"),
        skip_queryset_actions=True,
    ):
        url = None
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
        if tools_view_name:
            try:
                url = reverse(tools_view_name, kwargs={"tool": action_name})
            except NoReverseMatch:
                url = None
        if not url:
            base = f"admin:{model_admin.opts.app_label}_{model_admin.opts.model_name}_"
            try:
                url = reverse(base + action_name)
            except NoReverseMatch:
                try:
                    url = reverse(base + action_name.split("_")[0])
                except NoReverseMatch:
                    url = reverse(base + "changelist") + f"?action={action_name}"
        add_action(action_name, func, label, url)

    for action_name, func, label in iter_model_admin_named_actions(
        get_named_actions("get_dashboard_actions"),
        skip_queryset_actions=False,
    ):
        dashboard_url = getattr(func, "dashboard_url", None)
        if isinstance(dashboard_url, str):
            try:
                url = reverse(dashboard_url)
            except NoReverseMatch:
                url = dashboard_url
        else:
            url = ""
        add_action(action_name, func, label, url)

    if request is not None:
        action_cache[cache_key] = actions
    return actions


@register.simple_tag
def dashboard_model_status(app_label: str, model_name: str) -> dict | None:
    """Return dashboard rule status for a model when configured."""

    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None

    content_type = ContentType.objects.get_for_model(
        model, for_concrete_model=False
    )

    try:
        rule = DashboardRule.objects.select_related("content_type").get(
            content_type=content_type
        )
    except DashboardRule.DoesNotExist:
        return None

    try:
        return DashboardRule.get_cached_value(content_type, rule.evaluate)
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
            status_map[content_type.id] = DashboardRule.get_cached_value(
                content_type, rule.evaluate
            )
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
        concrete_model = getattr(model_cls, "_meta", None)
        if concrete_model is None:
            return None
        concrete_model = concrete_model.concrete_model
        if concrete_model in registry:
            return concrete_model
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
