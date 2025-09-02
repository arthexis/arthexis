import ast
import inspect
import textwrap

from django import template
from django.apps import apps
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.urls import NoReverseMatch, reverse

from core.user_data import UserDatum

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
            (n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
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
    for action_name, (func, _name, description) in model_admin.get_actions(request).items():
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
                url = (
                    reverse(base + "changelist") + f"?action={action_name}"
                )
        actions.append({"url": url, "label": description or _name.replace("_", " ")})
    return actions


@register.simple_tag
def admin_changelist_url(ct: ContentType) -> str:
    """Return the admin changelist URL for the given content type."""
    try:
        return reverse(f"admin:{ct.app_label}_{ct.model}_changelist")
    except NoReverseMatch:
        return ""


@register.simple_tag(takes_context=True)
def future_action_items(context):
    """Return deduplicated dashboard links for the current user.

    The list includes recent admin history entries, favorites and models with
    user data.  Each model appears at most once and is displayed using its
    plural name.
    """

    request = context.get("request")
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return []

    items = []
    seen = set()

    # Recently visited changelists (history)
    for entry in user.admin_history.all()[:10]:
        if entry.content_type_id in seen or not entry.url:
            continue
        items.append({"url": entry.url, "label": entry.admin_label})
        seen.add(entry.content_type_id)

    # Favorites
    favorites = user.favorites.select_related("content_type")
    for fav in favorites:
        ct = fav.content_type
        if ct.id in seen:
            continue
        model = ct.model_class()
        label = fav.custom_label or (
            model._meta.verbose_name_plural if model else ct.name
        )
        url = admin_changelist_url(ct)
        if url:
            items.append({"url": url, "label": label})
            seen.add(ct.id)

    # Models with user data
    ct_ids = UserDatum.objects.filter(user=user).values_list(
        "content_type_id", flat=True
    )
    for ct in ContentType.objects.filter(id__in=ct_ids).exclude(id__in=seen):
        model = ct.model_class()
        label = model._meta.verbose_name_plural if model else ct.name
        url = admin_changelist_url(ct)
        if url:
            items.append({"url": url, "label": label})
            seen.add(ct.id)

    return items


@register.simple_tag(takes_context=True)
def filtered_app_list(context, app_list):
    """Filter ``app_list`` to exclude models already shown in future actions.

    Models that appear in the user's admin history, favorites or user data are
    removed from the list to avoid duplicate links on the dashboard.
    """

    request = context.get("request")
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return app_list

    seen = set(
        user.admin_history.values_list("content_type_id", flat=True)[:10]
    )
    seen.update(user.favorites.values_list("content_type_id", flat=True))
    seen.update(
        UserDatum.objects.filter(user=user).values_list("content_type_id", flat=True)
    )

    filtered = []
    for app in app_list:
        models = []
        for model in app.get("models", []):
            try:
                ct = ContentType.objects.get_by_natural_key(
                    app["app_label"], model["object_name"].lower()
                )
            except ContentType.DoesNotExist:
                models.append(model)
                continue
            if ct.id in seen:
                continue
            models.append(model)
        if models:
            new_app = app.copy()
            new_app["models"] = models
            filtered.append(new_app)

    return filtered
