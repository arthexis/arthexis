"""Local app package public API.

Importing from :mod:`apps.locals` provides the same symbols as
:mod:`apps.locals.api` while deferring heavy imports until first use.
"""

from importlib import import_module

__all__ = [
    "EntityModelAdmin",
    "ImportExportAdminMixin",
    "UserDatumAdminMixin",
    "delete_user_fixture",
    "dump_user_fixture",
    "fixture_path",
    "load_local_seed_zips",
    "load_shared_user_fixtures",
    "load_user_fixtures",
    "patch_admin_import_export",
    "patch_admin_user_data_views",
    "patch_admin_user_datum",
    "resolve_fixture_user",
    "toggle_user_datum",
    "user_allows_user_data",
]


def __getattr__(name: str):
    if name in __all__:
        return getattr(import_module("apps.locals.api"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
