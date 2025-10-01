"""Expose legacy ``ocpp.tests`` module defined in ``tests.py``.

This project historically stored the bulk of the OCPP integration tests in a
module named ``ocpp/tests.py`` while also using ``ocpp/tests/`` as a package for
pytest-style unit tests.  Django's test discovery imports ``ocpp.tests`` as a
package which meant the legacy module was skipped and direct dotted paths such
as ``ocpp.tests.CSMSConsumerTests`` failed because the import machinery expected
``CSMSConsumerTests`` to be a submodule rather than a class within the legacy
module.

To keep backwards compatibility we load ``tests.py`` as a helper module and
re-export its public attributes from the package namespace.  We also register
``ocpp.tests.CSMSConsumerTests`` (and the module alias we load under) inside
``sys.modules`` so dotted imports used by Django's test runner resolve to the
legacy module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_legacy_module() -> object:
    """Load ``ocpp/tests.py`` exactly once and return the module object."""

    module_name = "ocpp._legacy_tests"
    if module_name in sys.modules:
        return sys.modules[module_name]

    legacy_path = Path(__file__).resolve().parents[1] / "tests.py"
    spec = importlib.util.spec_from_file_location(module_name, legacy_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"Unable to load legacy tests module from {legacy_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_legacy_tests = _load_legacy_module()
_exported_names: set[str] = set()

# Make the legacy module discoverable under dotted paths that Django and
# developers have historically used when targeting individual tests.
sys.modules.setdefault("ocpp.tests.CSMSConsumerTests", _legacy_tests)
sys.modules.setdefault("ocpp.tests.legacy", _legacy_tests)

# Re-export all public objects from ``tests.py`` so ``from ocpp.tests import``
# continues to behave as before while still supporting the package layout.
for _name in getattr(_legacy_tests, "__all__", None) or dir(_legacy_tests):
    if _name.startswith("_"):
        continue
    globals().setdefault(_name, getattr(_legacy_tests, _name))
    _exported_names.add(_name)


__all__ = sorted(_exported_names)
