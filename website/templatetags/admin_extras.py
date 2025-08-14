import ast
import inspect
import textwrap

from django import template
from django.apps import apps
from django.contrib import admin
from django.urls import reverse

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
        url = reverse(
            f"admin:{model_admin.opts.app_label}_{model_admin.opts.model_name}_changelist"
        ) + f"?action={action_name}"
        actions.append({"url": url, "label": description or _name.replace("_", " ")})
    return actions
