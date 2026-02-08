import importlib
import sys

import pytest


@pytest.mark.integration
def test_arthexis_lazy_app_import() -> None:
    target_module = "apps.flows"
    submodule = "apps.flows.node_workflow"
    saved_modules = {}
    alias_modules = ("flows", "arthexis.flows")
    for name in (submodule, target_module, "arthexis", *alias_modules):
        if name in sys.modules:
            saved_modules[name] = sys.modules.pop(name)

    try:
        arthexis = importlib.import_module("arthexis")

        assert target_module in sys.modules
        assert submodule not in sys.modules

        flows = getattr(arthexis, "flows")
        _ = flows.NodeWorkflow
        assert submodule in sys.modules
    finally:
        for name in (submodule, target_module, "arthexis", *alias_modules):
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
