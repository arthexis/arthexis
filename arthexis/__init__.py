import importlib
import sys

suite = importlib.import_module(".suite", __name__)
SuiteGateway = suite.SuiteGateway
RemoteObject = suite.RemoteObject
SuiteError = suite.SuiteError

for _name in ("config", "core", "nodes", "ocpp", "pages"):
    try:
        module = importlib.import_module(f".{_name}", __name__)
    except ModuleNotFoundError:  # pragma: no cover - defensive
        continue
    sys.modules.setdefault(_name, module)

__all__ = ["suite", "SuiteGateway", "RemoteObject", "SuiteError"]
