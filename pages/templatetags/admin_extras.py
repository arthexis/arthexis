import ast
import inspect
import textwrap
from datetime import timedelta
from pathlib import Path

from django import template
from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models import Model
from django.db.models.signals import post_delete, post_save
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils.text import capfirst
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.dispatch import receiver

from core.models import Lead, RFID, Todo
from ocpp.models import Charger
from core.entity import Entity, user_data_flag_updated

register = template.Library()


USER_DATA_MODELS_CACHE_KEY = "pages:future_action_items:user_data_models"
USER_DATA_MODELS_CACHE_TIMEOUT = getattr(settings, "USER_DATA_MODELS_CACHE_TIMEOUT", 300)


def _get_user_data_model_labels() -> set[str]:
    """Return cached model labels with ``is_user_data`` rows."""

    timeout = getattr(settings, "USER_DATA_MODELS_CACHE_TIMEOUT", USER_DATA_MODELS_CACHE_TIMEOUT)
    cache_entry: dict[str, object] | None = cache.get(USER_DATA_MODELS_CACHE_KEY)
    now = timezone.now()
    if cache_entry:
        labels = cache_entry.get("labels")
        timestamp = cache_entry.get("timestamp")
        if isinstance(labels, (list, tuple, set)) and timestamp is not None:
            if timeout:
                if now - timestamp < timedelta(seconds=timeout):
                    return set(labels)
            else:
                return set(labels)

    labels: set[str] = set()
    for model, _ in admin.site._registry.items():
        if model is Todo or not issubclass(model, Entity):
            continue
        if model.objects.filter(is_user_data=True).exists():
            labels.add(model._meta.label_lower)

    cache.set(
        USER_DATA_MODELS_CACHE_KEY,
        {"labels": list(labels), "timestamp": now},
        timeout=timeout,
    )
    return labels


def _invalidate_user_data_model_cache() -> None:
    cache.delete(USER_DATA_MODELS_CACHE_KEY)


@receiver(post_save)
def _invalidate_user_data_models_on_save(sender, instance, created, update_fields, **kwargs):
    if not isinstance(instance, Entity):
        return
    if created and not instance.is_user_data:
        return
    if update_fields and "is_user_data" not in update_fields:
        return
    _invalidate_user_data_model_cache()


@receiver(post_delete)
def _invalidate_user_data_models_on_delete(sender, instance, **kwargs):
    if not isinstance(instance, Entity):
        return
    if not instance.is_user_data:
        return
    _invalidate_user_data_model_cache()


@receiver(user_data_flag_updated)
def _invalidate_user_data_models_on_update(sender, **kwargs):
    if not issubclass(sender, Entity):
        return
    _invalidate_user_data_model_cache()


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
    seen = set()

    def add_action(action_name, func, label, url):
        if not url:
            return
        actions.append({"url": url, "label": label})
        seen.add(action_name)

    for action_name, (func, _name, description) in model_admin.get_actions(
        request
    ).items():
        if action_name == "delete_selected" or uses_queryset(func):
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

    changelist_getter = getattr(model_admin, "get_changelist_actions", None)
    if callable(changelist_getter):
        try:
            changelist_actions = changelist_getter(request)
        except TypeError:
            changelist_actions = changelist_getter()
        for action_name in changelist_actions or []:
            if action_name in seen:
                continue
            func = getattr(model_admin, action_name, None)
            if func is None or uses_queryset(func):
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
                        tools_view_name = getattr(
                            model_admin, "tools_view_name", None
                        )
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

    return actions


@register.simple_tag
def admin_changelist_url(ct: ContentType) -> str:
    """Return the admin changelist URL for the given content type."""
    try:
        return reverse(f"admin:{ct.app_label}_{ct.model}_changelist")
    except NoReverseMatch:
        return ""


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
def lead_open_count(context, app_label: str, model_name: str):
    """Return the number of open leads for the given model."""

    cache = context.setdefault("_lead_open_counts", {})
    cache_key = f"{app_label}.{model_name}".lower()
    if cache_key in cache:
        return cache[cache_key]

    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        cache[cache_key] = None
        return None

    concrete = model._meta.concrete_model
    if not issubclass(concrete, Lead):
        cache[cache_key] = None
        return None

    concrete_key = concrete._meta.label_lower
    if concrete_key in cache:
        count = cache[concrete_key]
    else:
        try:
            open_value = concrete.Status.OPEN
        except AttributeError:
            count = None
        else:
            count = (
                concrete._default_manager.filter(status=open_value).count()
            )
        cache[concrete_key] = count

    cache[cache_key] = count
    return count


@register.simple_tag(takes_context=True)
def rfid_release_stats(context):
    """Return release statistics for the RFID model."""

    cache_key = "_rfid_release_stats"
    stats = context.get(cache_key)
    if stats is None:
        counts = RFID.objects.aggregate(
            total=Count("pk"),
            released_allowed=Count(
                "pk", filter=Q(released=True, allowed=True)
            ),
        )
        stats = {
            "released_allowed": counts.get("released_allowed") or 0,
            "total": counts.get("total") or 0,
        }
        context[cache_key] = stats
    return stats


@register.simple_tag(takes_context=True)
def charger_availability_stats(context):
    """Return availability statistics for the Charger model."""

    cache_key = "_charger_availability_stats"
    stats = context.get(cache_key)
    if stats is None:
        available = Charger.objects.filter(last_status__iexact="Available")
        available_with_cp_number = available.filter(
            connector_id__isnull=False
        ).count()

        available_without_cp_number = available.filter(connector_id__isnull=True)
        has_connector = Charger.objects.filter(
            charger_id=OuterRef("charger_id"),
            connector_id__isnull=False,
        )
        missing_connector_count = available_without_cp_number.annotate(
            has_connector=Exists(has_connector)
        ).filter(has_connector=False).count()

        available_total = available_with_cp_number + missing_connector_count
        stats = {
            "available_total": available_total,
            "available_with_cp_number": available_with_cp_number,
            "available_missing_cp_number": missing_connector_count,
        }
        context[cache_key] = stats
    return stats


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
    user_data_labels = _get_user_data_model_labels()
    for model, model_admin in admin.site._registry.items():
        if model is Todo or not issubclass(model, Entity):
            continue
        if model._meta.label_lower not in user_data_labels:
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

    active_todos = list(
        Todo.objects.filter(is_deleted=False, done_on__isnull=True)
    )

    def _serialize(todo: Todo, *, completed: bool):
        details = (todo.request_details or "").strip()
        condition = (todo.on_done_condition or "").strip()
        data = {
            "url": reverse("todo-focus", args=[todo.pk]),
            "label": todo.request,
            "details": details,
            "condition": condition,
            "completed": completed,
        }
        if completed:
            data["done_on"] = todo.done_on
        else:
            data["done_url"] = reverse("todo-done", args=[todo.pk])
        return data

    todos: list[dict[str, object]] = [_serialize(todo, completed=False) for todo in active_todos]

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
