from importlib import import_module
import sys

from django.apps import apps as django_apps

if django_apps.is_installed("apps.ops"):
    _module = import_module("apps.ops.admin_notice")
    sys.modules[__name__] = _module
else:
    def __getattr__(name: str):
        if name == "AdminNotice":
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}; apps.ops is not installed"
            )
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
