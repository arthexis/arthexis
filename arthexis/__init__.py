import importlib
import importlib.util
import pkgutil
import sys
from types import ModuleType

import apps as _apps


def _discover_app_modules() -> dict[str, str]:
    modules: dict[str, str] = {}
    for _finder, module_name, _ispkg in pkgutil.iter_modules(
        _apps.__path__, prefix="apps."
    ):
        short_name = module_name.partition(".")[2]
        modules[short_name] = module_name

    return modules


_APP_MODULES = _discover_app_modules()


def _lazy_import(module_name: str) -> ModuleType | None:
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.loader is None:
        return None

    loader = importlib.util.LazyLoader(spec.loader)
    spec.loader = loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(module_name, module)
    loader.exec_module(module)
    return module


def _install_lazy_aliases() -> None:
    for short_name, module_name in _APP_MODULES.items():
        module = sys.modules.get(module_name) or _lazy_import(module_name)
        if module is None:
            continue

        sys.modules.setdefault(f"{__name__}.{short_name}", module)


def _load_app(short_name: str) -> ModuleType:
    module_name = _APP_MODULES[short_name]
    module = sys.modules.get(module_name)
    if module is None:
        module = importlib.import_module(module_name)

    sys.modules.setdefault(short_name, module)
    sys.modules.setdefault(f"{__name__}.{short_name}", module)
    globals()[short_name] = module
    return module


def __getattr__(name: str) -> ModuleType:
    if name in _APP_MODULES:
        return _load_app(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_APP_MODULES.keys()))


__all__ = list(_APP_MODULES.keys())
_install_lazy_aliases()
