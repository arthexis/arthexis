"""Tests for logging requests that miss or fail."""

from __future__ import annotations

import logging.config
from pathlib import Path

import pytest
from django.http import HttpResponse, HttpResponseNotFound
from django.test import Client, override_settings
from django.urls import path

from apps.loggers import build_logging_settings


urlpatterns = [
    path("server-error/", lambda _request: (_ for _ in ()).throw(RuntimeError("boom"))),
]


def simple_404(_request, _exception=None) -> HttpResponse:
    return HttpResponseNotFound()


def simple_500(_request) -> HttpResponse:
    return HttpResponse(status=500)


handler404 = "tests.test_page_miss_logging.simple_404"
handler500 = "tests.test_page_miss_logging.simple_500"

def test_page_misses_handler_is_configured(tmp_path: Path) -> None:
    """Logging config should include the dedicated page miss handler."""

    log_dir, _log_file_name, logging_config = build_logging_settings(
        tmp_path, debug_enabled=False
    )

    handler = logging_config["handlers"].get("page_misses_file")
    assert handler is not None
    assert handler["filename"] == str(log_dir / "page_misses.log")
    assert handler["level"] == "INFO"

    logger_settings = logging_config["loggers"].get("page_misses")
    assert logger_settings is not None
    assert logger_settings["handlers"] == ["page_misses_file"]
    assert logger_settings["propagate"] is False


@pytest.mark.django_db
def test_requests_with_404_and_500_are_logged(tmp_path: Path, settings) -> None:
    """Requests returning 404 or 500 should be written to the page misses log."""

    log_dir, _log_file_name, logging_config = build_logging_settings(
        tmp_path, debug_enabled=settings.DEBUG
    )
    settings.LOG_DIR = log_dir
    settings.LOGGING = logging_config
    logging.config.dictConfig(logging_config)

    with override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=["config.middleware.PageMissLoggingMiddleware"],
    ):
        local_client = Client()
        local_client.raise_request_exception = False

        missing_path = "/does-not-exist/"
        local_client.get(missing_path)
        local_client.get("/server-error/")

    log_file = log_dir / "page_misses.log"
    assert log_file.exists()

    content = log_file.read_text()
    assert "GET /does-not-exist/ -> 404" in content
    assert "GET /server-error/ -> 500" in content
