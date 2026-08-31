"""Regression coverage for OCPP websocket route boundaries."""

import pytest
from django.urls import Resolver404
from django.urls.resolvers import RegexPattern, URLResolver

from apps.ocpp.consumers import CSMSConsumer
from apps.ocpp.consumers.base.identity import IdentityMixin
from apps.ocpp.routing import websocket_urlpatterns as ocpp_websocket_urlpatterns


def _resolve(patterns, path: str):
    resolver = URLResolver(RegexPattern(r"^"), patterns)
    return resolver.resolve(path)


@pytest.mark.parametrize(
    ("path", "cid"),
    [
        ("base/ocpp/CP-ROUTE/", "CP-ROUTE"),
        ("base/ws/ocpp/CP-ROUTE/", "CP-ROUTE"),
        ("base/ws/CP-ROUTE/", "CP-ROUTE"),
        ("base/CP-ROUTE/", "CP-ROUTE"),
        ("region/site/ocpp/CP-ROUTE/", "CP-ROUTE"),
        ("region/site/ws/ocpp/CP-ROUTE/", "CP-ROUTE"),
        ("region/site/ws/CP-ROUTE/", "CP-ROUTE"),
        ("region/site/CP-ROUTE/", "CP-ROUTE"),
        ("ocpp/CP-ROUTE/", "CP-ROUTE"),
        ("ws/ocpp/CP-ROUTE/", "CP-ROUTE"),
        ("base/ocpp/", ""),
        ("base/ws/ocpp/", ""),
        ("region/site/ocpp/", ""),
        ("region/site/ws/ocpp/", ""),
        ("ocpp/", ""),
        ("ws/ocpp/", ""),
        ("ws/CP-ROUTE/", "CP-ROUTE"),
        ("CP-ROUTE", "CP-ROUTE"),
        ("", ""),
    ],
)
def test_ocpp_websocket_routes_keep_supported_charge_point_paths(path: str, cid: str):
    match = _resolve(ocpp_websocket_urlpatterns, path)

    assert getattr(match.func, "consumer_class", None) is CSMSConsumer
    assert (match.kwargs.get("cid") or "") == cid


@pytest.mark.parametrize(
    "path",
    [
        "ws/pages/chat/",
        "ws/nodes/events/",
        "base/ws/pages/chat/",
        "region/site/ws/pages/chat/",
        "base/ws",
        "base/ws/",
        "region/site/ws",
        "region/site/ws/",
        "ws",
        "ws/",
    ],
)
def test_ocpp_websocket_routes_reject_nested_ws_namespaces(path: str):
    with pytest.raises(Resolver404):
        _resolve(ocpp_websocket_urlpatterns, path)


class _IdentityProbe(IdentityMixin):
    pass


@pytest.mark.parametrize(
    "path",
    ["/ocpp/", "/ws/ocpp/", "/base/ocpp/", "/base/ws/ocpp/"],
)
def test_ocpp_namespace_paths_use_query_identity(path: str):
    probe = _IdentityProbe()
    probe.scope = {
        "path": path,
        "query_string": b"cid=CP-QUERY",
        "url_route": {"kwargs": {}},
    }

    assert probe._extract_serial_identifier() == "CP-QUERY"
    assert probe.serial_source == "query"


@pytest.mark.parametrize(
    "path",
    ["/ocpp/", "/ws/ocpp/", "/base/ocpp/", "/base/ws/ocpp/"],
)
def test_ocpp_namespace_paths_without_query_do_not_use_namespace_as_serial(path: str):
    probe = _IdentityProbe()
    probe.scope = {
        "path": path,
        "query_string": b"",
        "url_route": {"kwargs": {}},
    }

    assert probe._extract_serial_identifier() == ""
