from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from config.middleware import ActiveAppMiddleware
from config.request_utils import get_request_log_context, reset_request_log_context, set_request_log_context
from utils.loggers.config import resolve_log_formatter
from utils.loggers.filters import RequestContextFilter
from utils.loggers.json_formatter import JSONFormatter


def _build_request(**kwargs):
    resolver_match = kwargs.pop("resolver_match", None)
    return SimpleNamespace(
        META=kwargs.pop("META", {}),
        GET=kwargs.pop("GET", {}),
        POST=kwargs.pop("POST", {}),
        resolver_match=resolver_match,
        **kwargs,
    )


def test_resolve_log_formatter_defaults_to_text(monkeypatch):
    monkeypatch.delenv("ARTHEXIS_LOG_FORMAT", raising=False)
    assert resolve_log_formatter() == "standard"


def test_resolve_log_formatter_json_mode(monkeypatch):
    monkeypatch.setenv("ARTHEXIS_LOG_FORMAT", "json")
    assert resolve_log_formatter() == "json"


def test_request_context_filter_applies_request_identifiers():
    request = _build_request(
        META={"HTTP_X_REQUEST_ID": "req-123"},
        resolver_match=SimpleNamespace(kwargs={"charger_id": "CP-1", "session_id": "S-9"}),
    )
    token = set_request_log_context(request, node_id="42")
    record = logging.LogRecord(
        name="apps.forwarder.ocpp",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    try:
        accepted = RequestContextFilter().filter(record)
    finally:
        reset_request_log_context(token)

    assert accepted is True
    assert record.request_id == "req-123"
    assert record.node_id == "42"
    assert record.charger_id == "CP-1"
    assert record.session_id == "S-9"


def test_set_request_log_context_does_not_touch_post_body():
    class _RaisingPOST:
        def get(self, _key):
            raise AssertionError("POST should not be accessed while binding log context")

    request = _build_request(
        META={"HTTP_X_REQUEST_ID": "req-123"},
        resolver_match=SimpleNamespace(kwargs={"charger_id": "CP-1"}),
        POST=_RaisingPOST(),
    )

    token = set_request_log_context(request, node_id="42")
    try:
        context = get_request_log_context()
    finally:
        reset_request_log_context(token)

    assert context["charger_id"] == "CP-1"
    assert context["session_id"] == ""


def test_active_app_middleware_binds_context_after_resolving_route(monkeypatch):
    middleware = ActiveAppMiddleware(
        lambda request: SimpleNamespace(
            status_code=200,
            context_snapshot=get_request_log_context().copy(),
            active_app=request.active_app,
        )
    )

    monkeypatch.setattr(
        "config.middleware.resolve",
        lambda _path: SimpleNamespace(kwargs={"charger_id": "CP-2", "session_id": "S-2"}),
    )
    monkeypatch.setattr("config.middleware.get_site", lambda _request: None)
    monkeypatch.setattr(
        "config.middleware.Node.get_local",
        lambda: SimpleNamespace(pk=5, role=SimpleNamespace(name="Terminal")),
    )
    request = _build_request(path_info="/ocpp/c/CP-2/", resolver_match=None)

    response = middleware(request)

    assert response.context_snapshot["node_id"] == "5"
    assert response.context_snapshot["charger_id"] == "CP-2"
    assert response.context_snapshot["session_id"] == "S-2"


def test_json_formatter_outputs_stable_fields():
    record = logging.LogRecord(
        name="apps.forwarder.ocpp",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="connected",
        args=(),
        exc_info=None,
    )
    record.app = "Terminal"
    record.hostname = "host-a"
    record.request_id = "req-1"
    record.node_id = "node-1"
    record.charger_id = "CP-10"
    record.session_id = "sess-10"

    payload = json.loads(JSONFormatter().format(record))

    assert sorted(payload.keys()) == [
        "app",
        "charger_id",
        "hostname",
        "level",
        "logger",
        "message",
        "node_id",
        "process",
        "request_id",
        "session_id",
        "thread",
        "timestamp",
    ]
    assert payload["logger"] == "apps.forwarder.ocpp"
    assert payload["message"] == "connected"
