"""System admin entrypoints."""

__all__ = ["patch_admin_system_view", "_system_view"]


def __getattr__(name: str):
    if name == "patch_admin_system_view":
        from .admin_views import patch_admin_system_view

        return patch_admin_system_view
    if name == "_system_view":
        from .admin_views import _system_view

        return _system_view
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
