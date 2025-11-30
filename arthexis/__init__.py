import importlib
import sys

for _name in ("config", "nodes", "ocpp", "pages"):
    try:
        module = importlib.import_module(f".{_name}", __name__)
    except ModuleNotFoundError:  # pragma: no cover - defensive
        continue
    sys.modules.setdefault(_name, module)

__all__ = []
