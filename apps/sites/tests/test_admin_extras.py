from __future__ import annotations

import sys
from types import SimpleNamespace

from django.template import Context
from django.utils.safestring import SafeString

from apps.sites.templatetags import admin_extras


def test_admin_staff_tasks_returns_empty_when_actions_app_is_missing(monkeypatch):
    def fail_import(_name, globals=None, locals=None, fromlist=(), level=0):
        if _name == "apps.actions.staff_tasks":
            raise AssertionError("apps.actions.staff_tasks should not be imported")
        return original_import(_name, globals, locals, fromlist, level)

    original_import = __import__
    monkeypatch.setattr(admin_extras.apps, "is_installed", lambda app: False)
    monkeypatch.setattr("builtins.__import__", fail_import)

    assert admin_extras.admin_staff_tasks(Context({})) == []


def test_admin_staff_tasks_delegates_when_actions_app_is_installed(monkeypatch):
    class Request:
        user = object()

    expected_tasks = [{"label": "Review", "url": "/admin/actions/"}]

    monkeypatch.setattr(admin_extras.apps, "is_installed", lambda app: True)
    monkeypatch.setitem(
        sys.modules,
        "apps.actions.staff_tasks",
        SimpleNamespace(visible_staff_tasks_for_user=lambda user: expected_tasks),
    )

    assert (
        admin_extras.admin_staff_tasks(Context({"request": Request()}))
        == expected_tasks
    )


def test_include_if_exists_returns_empty_when_template_is_missing(monkeypatch):
    def missing_template(_template_name):
        raise admin_extras.TemplateDoesNotExist("missing")

    monkeypatch.setattr(admin_extras.loader, "get_template", missing_template)

    assert admin_extras.include_if_exists(Context({}), "missing.html") == ""


def test_include_if_exists_returns_safe_rendered_template(monkeypatch):
    class TemplateStub:
        def render(self, context, request=None):
            assert context["value"] == "ok"
            assert request == "request"
            return "<div>ok</div>"

    monkeypatch.setattr(
        admin_extras.loader,
        "get_template",
        lambda _template_name: TemplateStub(),
    )

    result = admin_extras.include_if_exists(
        Context({"request": "request", "value": "ok"}),
        "present.html",
    )

    assert isinstance(result, SafeString)
    assert str(result) == "<div>ok</div>"
