from types import SimpleNamespace

from apps.sites.context_processors import (
    _limit_anon_ocpp_landings,
    _sort_module_landings,
)


def test_sort_module_landings_prioritizes_ocpp_navigation_paths():
    landings = [
        SimpleNamespace(path="/ocpp/charge-point-models/"),
        SimpleNamespace(path="/ocpp/cpms/dashboard/"),
        SimpleNamespace(path="/ocpp/evcs/simulator/"),
    ]

    ordered_paths = [landing.path for landing in _sort_module_landings("/ocpp/", landings)]

    assert ordered_paths == [
        "/ocpp/cpms/dashboard/",
        "/ocpp/evcs/simulator/",
        "/ocpp/charge-point-models/",
    ]


def test_sort_module_landings_keeps_non_ocpp_order():
    landings = [
        SimpleNamespace(path="/docs/library/"),
        SimpleNamespace(path="/docs/help/"),
    ]

    ordered_paths = [landing.path for landing in _sort_module_landings("/docs/", landings)]

    assert ordered_paths == [
        "/docs/library/",
        "/docs/help/",
    ]


def test_limit_anon_ocpp_landings_handles_request_without_user():
    module = SimpleNamespace(path="/ocpp/", menu="Charge Points")
    landings = [
        SimpleNamespace(path="/ocpp/cpms/dashboard/"),
        SimpleNamespace(path="/ocpp/charge-point-models/"),
    ]

    filtered = _limit_anon_ocpp_landings(module, SimpleNamespace(), landings)

    assert [landing.path for landing in filtered] == ["/ocpp/charge-point-models/"]
    assert str(module.menu) == "Supported CP Models"
