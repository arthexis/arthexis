import importlib

from apps.ocpp import store


def test_store_submodule_aliases_preserve_mapping_exports():
    logs_module = importlib.import_module("apps.ocpp.store.logs")
    pending_calls_module = importlib.import_module("apps.ocpp.store.pending_calls")
    state_module = importlib.import_module("apps.ocpp.store.state")

    assert store.logs is logs_module.logs
    assert store.pending_calls is pending_calls_module.pending_calls
    assert store.transactions is state_module.transactions


def test_store_all_remains_function_focused():
    assert "logs" not in store.__all__
    assert "pending_calls" not in store.__all__
    assert "transactions" not in store.__all__
