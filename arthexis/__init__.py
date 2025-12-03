import importlib
import pkgutil
import sys

import apps as _apps


def _import_apps():
    exported = []

    for _finder, module_name, _ispkg in pkgutil.iter_modules(
        _apps.__path__, prefix="apps."
    ):
        module = importlib.import_module(module_name)
        short_name = module_name.partition(".")[2]
        sys.modules.setdefault(short_name, module)
        sys.modules.setdefault(f"{__name__}.{short_name}", module)
        globals().setdefault(short_name, module)
        exported.append(short_name)

    return exported


__all__ = _import_apps()
