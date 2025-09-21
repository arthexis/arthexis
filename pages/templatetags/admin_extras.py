import ast
import inspect
import textwrap
from pathlib import Path

from django import template
from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.models import Model
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils.text import capfirst

from core.models import ReleaseManager, Todo
from core.entity import Entity

register = template.Library()


@register.simple_tag(takes_context=True)
def model_admin_actions(context, app_label, model_name):
    """Return available admin actions for the given model.

    Only custom actions are returned; the default ``delete_selected`` action is
    ignored. Each action is represented as a dict with ``url`` and ``label``
    keys so templates can render them as links.
    """
    request = context.get("request")
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return []
    model_admin = admin.site._registry.get(model)
    if not model_admin:
        return []

    def uses_queryset(func):
        func = inspect.unwrap(func)
        try:
            source = textwrap.dedent(inspect.getsource(func))
        except (OSError, TypeError):
            return True
        try:
            tree = ast.parse(source)
        except SyntaxError:
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
        return finder.found

    actions = []
    for action_name, (func, _name, description) in model_admin.get_actions(
        request
    ).items():
        if action_name == "delete_selected" or uses_queryset(func):
            continue
        url = None
        base = f"admin:{model_admin.opts.app_label}_{model_admin.opts.model_name}_"
        try:
            url = reverse(base + action_name)
        except NoReverseMatch:
            try:
                url = reverse(base + action_name.split("_")[0])
            except NoReverseMatch:
                url = reverse(base + "changelist") + f"?action={action_name}"
        actions.append({"url": url, "label": description or _name.replace("_", " ")})
    return actions


@register.simple_tag
def admin_changelist_url(ct: ContentType) -> str:
    """Return the admin changelist URL for the given content type."""
    try:
        return reverse(f"admin:{ct.app_label}_{ct.model}_changelist")
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
        concrete_model = getattr(model_cls, "_meta", None)
        if concrete_model is None:
            return None
        concrete_model = concrete_model.concrete_model
        if concrete_model in registry:
            return concrete_model
        return None

    def add_model(model_cls):
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
        })
        seen.add(label_lower)

    for parent in opts.get_parent_list():
        add_model(parent)

    for field in opts.get_fields(include_parents=True, include_hidden=True):
        if not getattr(field, "is_relation", False):
            continue
        related_model = getattr(field, "related_model", None)
        if related_model is None:
            continue
        add_model(related_model)

    related.sort(key=lambda item: item["label"])
    return related


@register.simple_tag(takes_context=True)
def model_db_status(context, app_label: str, model_name: str) -> bool:
    """Return ``True`` if the model's database table exists.

    The table list is cached on the template context to avoid repeated
    introspection queries within a single request.
    """
    cache_key = "_model_status_tables"
    tables = context.get(cache_key)
    if tables is None:
        tables = set(connection.introspection.table_names())
        context[cache_key] = tables
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return False
    return model._meta.db_table in tables


@register.simple_tag(takes_context=True)
def future_action_items(context):
    """Return dashboard links and TODOs for the current user.

    Returns a dict with ``models`` and ``todos`` lists. The ``models`` list
    includes recent admin history entries, favorites and models with user
    data. The ``todos`` list contains Release Manager tasks from fixtures.
    """

    request = context.get("request")
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"models": [], "todos": []}

    badge_node = context.get("badge_node")
    node_role_name = ""
    if badge_node:
        role = getattr(badge_node, "role", None)
        node_role_name = getattr(role, "name", "") if role else ""

    model_data = {}
    first_seen = 0
    todo_ct = ContentType.objects.get_for_model(Todo)

    def register_model(ct: ContentType, label: str, url: str, priority: int) -> None:
        nonlocal first_seen
        if not ct or ct.id == todo_ct.id or not url:
            return
        entry = model_data.get(ct.id)
        if entry is None:
            entry = {
                "url": url,
                "label": label,
                "count": 0,
                "priority": priority,
                "first_seen": first_seen,
            }
            model_data[ct.id] = entry
            first_seen += 1
        else:
            if priority < entry["priority"]:
                entry.update({"url": url, "label": label, "priority": priority})
        entry["count"] += 1

    # Recently visited changelists (history)
    history = user.admin_history.select_related("content_type").all()[:10]
    for entry in history:
        ct = entry.content_type
        if not ct or not entry.url:
            continue
        register_model(ct, entry.admin_label, entry.url, priority=0)

    # Favorites
    favorites = user.favorites.select_related("content_type")
    for fav in favorites:
        ct = fav.content_type
        model = ct.model_class()
        label = fav.custom_label or (
            model._meta.verbose_name_plural if model else ct.name
        )
        url = admin_changelist_url(ct)
        if url:
            register_model(ct, label, url, priority=1)

    # Models with user data
    for model, model_admin in admin.site._registry.items():
        if model is Todo or not issubclass(model, Entity):
            continue
        if not model.objects.filter(is_user_data=True).exists():
            continue
        ct = ContentType.objects.get_for_model(model)
        label = model._meta.verbose_name_plural
        url = admin_changelist_url(ct)
        if url:
            register_model(ct, label, url, priority=2)

    sorted_models = sorted(
        model_data.values(),
        key=lambda item: (
            -item["count"],
            item["priority"],
            item["first_seen"],
            item["label"],
        ),
    )
    model_items = [
        {"url": item["url"], "label": item["label"]} for item in sorted_models[:4]
    ]

    todos: list[dict[str, str]] = []
    if node_role_name == "Terminal" and user.has_profile(ReleaseManager):
        todos = [
            {
                "url": todo.url or reverse("admin:core_todo_change", args=[todo.pk]),
                "label": todo.request,
                "details": todo.request_details,
                "done_url": reverse("todo-done", args=[todo.pk]),
            }
            for todo in Todo.objects.filter(is_deleted=False, done_on__isnull=True)
        ]

    return {"models": model_items, "todos": todos}


@register.simple_tag(takes_context=True)
def celery_feature_enabled(context) -> bool:
    """Return ``True`` when Celery support is enabled for the current node."""

    node = context.get("badge_node")
    if node is not None and hasattr(node, "has_feature"):
        try:
            if node.has_feature("celery-queue"):
                return True
        except Exception:  # pragma: no cover - defensive
            pass

    lock_path = Path(settings.BASE_DIR) / "locks" / "celery.lck"
    return lock_path.exists()
