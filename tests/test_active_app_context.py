"""Tests for active app context propagation and middleware behavior."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from apps.loggers.filters import DebugAppFilter
from config.active_app import active_app, get_active_app
from config.middleware import ActiveAppMiddleware


@pytest.fixture
def request_factory() -> RequestFactory:
    """Return a request factory for middleware tests."""

    return RequestFactory()


def test_active_app_middleware_restores_previous_context(
    monkeypatch: pytest.MonkeyPatch, request_factory: RequestFactory
) -> None:
    """Middleware should restore the prior active app instead of forcing hostname."""

    monkeypatch.setattr("config.middleware.get_site", lambda _request: SimpleNamespace(name="SiteA"))
    monkeypatch.setattr("config.middleware.Node.get_local", lambda: None)

    with active_app("outer-app"):
        seen: list[str] = []

        def _get_response(_request):
            seen.append(get_active_app())
            return HttpResponse("ok")

        middleware = ActiveAppMiddleware(_get_response)
        request = request_factory.get("/")
        response = middleware(request)

        assert response.status_code == 200
        assert seen == ["SiteA"]
        assert request.active_app == "SiteA"
        assert get_active_app() == "outer-app"


def test_active_app_middleware_restores_previous_context_on_exception(
    monkeypatch: pytest.MonkeyPatch, request_factory: RequestFactory
) -> None:
    """Middleware should restore context even when downstream code raises an error."""

    monkeypatch.setattr("config.middleware.get_site", lambda _request: SimpleNamespace(name="SiteB"))
    monkeypatch.setattr("config.middleware.Node.get_local", lambda: None)

    with active_app("outer-app"):

        def _raise(_request):
            raise RuntimeError("boom")

        middleware = ActiveAppMiddleware(_raise)
        request = request_factory.get("/")

        with pytest.raises(RuntimeError, match="boom"):
            middleware(request)

        assert get_active_app() == "outer-app"


def test_debug_filter_uses_contextvar_per_task_for_log_attribution() -> None:
    """Concurrent tasks should keep independent active app names for debug filtering."""

    debug_filter = DebugAppFilter(debug_value="alpha,beta")

    async def _check_app(app_name: str) -> tuple[str, bool]:
        with active_app(app_name):
            await asyncio.sleep(0)
            record = logging.LogRecord(
                name="test",
                level=logging.DEBUG,
                pathname=__file__,
                lineno=1,
                msg="debug",
                args=(),
                exc_info=None,
            )
            allowed = debug_filter.filter(record)
            return get_active_app(), allowed

    async def _run() -> tuple[tuple[str, bool], tuple[str, bool]]:
        return await asyncio.gather(_check_app("alpha"), _check_app("gamma"))

    first, second = asyncio.run(_run())

    assert first == ("alpha", True)
    assert second == ("gamma", False)


