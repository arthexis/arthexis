"""Tests for logging requests that miss expected pages."""

import logging

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from config.middleware import PageMissLoggingMiddleware


@pytest.fixture
def request_factory() -> RequestFactory:
    return RequestFactory()


def test_page_miss_logging_middleware_records_404(
    caplog: pytest.LogCaptureFixture, request_factory: RequestFactory
) -> None:
    """Requests returning 404 should be written to the page_misses logger."""

    request = request_factory.get("/missing?page=2")
    middleware = PageMissLoggingMiddleware(lambda _request: HttpResponse(status=404))
    logger = logging.getLogger("page_misses")
    logger.addHandler(caplog.handler)

    try:
        with caplog.at_level(logging.INFO, logger="page_misses"):
            middleware(request)
    finally:
        logger.removeHandler(caplog.handler)

    assert "GET /missing?page=2 -> 404" in caplog.text


def test_page_miss_logging_middleware_records_500_on_exception(
    caplog: pytest.LogCaptureFixture, request_factory: RequestFactory
) -> None:
    """Unhandled errors should still record a page miss entry before bubbling."""

    request = request_factory.get("/explode")

    def _raise(_request):
        raise RuntimeError("boom")

    middleware = PageMissLoggingMiddleware(_raise)
    logger = logging.getLogger("page_misses")
    logger.addHandler(caplog.handler)

    try:
        with caplog.at_level(logging.INFO, logger="page_misses"):
            with pytest.raises(RuntimeError):
                middleware(request)
    finally:
        logger.removeHandler(caplog.handler)

    assert "GET /explode -> 500" in caplog.text
