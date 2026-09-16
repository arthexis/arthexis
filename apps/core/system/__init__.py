"""System admin entrypoints."""

from importlib import import_module

__all__ = ["patch_admin_system_view", "_system_view"]

_RETIRED_NGINX_REPORT_ROUTE = "system-nginx-report"


def _load_admin_views():
    """Load admin views while retiring the legacy nginx report route."""

    from . import ui

    def _retired_nginx_report():
        raise RuntimeError("The legacy nginx report has been retired.")

    # ``admin_views`` historically imported this symbol at module import time.
    # Provide it only while importing that module so older import wiring cannot
    # block Django startup; it is removed immediately and the route is never
    # registered.
    setattr(ui, "build_nginx_report", _retired_nginx_report)
    try:
        module = import_module("apps.core.system.admin_views")
    finally:
        if getattr(ui, "build_nginx_report", None) is _retired_nginx_report:
            delattr(ui, "build_nginx_report")

    module.TASK_PANEL_ROUTES[:] = [
        route
        for route in module.TASK_PANEL_ROUTES
        if route.name != _RETIRED_NGINX_REPORT_ROUTE
    ]
    module.__dict__.pop("build_nginx_report", None)
    module.__dict__.pop("_system_nginx_report_view", None)
    return module


def __getattr__(name: str):
    if name == "patch_admin_system_view":
        return _load_admin_views().patch_admin_system_view
    if name == "_system_view":
        return _load_admin_views()._system_view
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
