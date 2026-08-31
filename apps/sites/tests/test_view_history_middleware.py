from unittest.mock import Mock

import pytest
from django.db.utils import OperationalError
from django.http import HttpResponse
from django.test import RequestFactory

from apps.sites.middleware import ViewHistoryMiddleware

pytestmark = [pytest.mark.django_db]


def test_view_history_database_failure_does_not_break_response(monkeypatch):
    request = RequestFactory().get("/analytics-safe/")
    middleware = ViewHistoryMiddleware(lambda _request: HttpResponse("ok", status=200))

    mock_logger = Mock()
    monkeypatch.setattr("apps.sites.middleware.logger", mock_logger)
    monkeypatch.setattr(
        "apps.sites.middleware.ViewHistory.objects.create",
        Mock(side_effect=OperationalError("db unavailable")),
    )

    response = middleware(request)

    assert response.status_code == 200
    assert mock_logger.debug.call_count == 1
    assert "OperationalError" == mock_logger.debug.call_args.args[1]
    assert "/analytics-safe/" == mock_logger.debug.call_args.args[2]


def test_record_visit_failure_does_not_mask_original_exception(monkeypatch):
    request = RequestFactory().get("/analytics-error/")
    middleware = ViewHistoryMiddleware(
        lambda _request: (_ for _ in ()).throw(ValueError("view failed"))
    )

    mock_logger = Mock()
    monkeypatch.setattr("apps.sites.middleware.logger", mock_logger)
    monkeypatch.setattr(
        middleware,
        "_record_visit",
        Mock(side_effect=RuntimeError("analytics failed")),
    )

    with pytest.raises(ValueError, match="view failed"):
        middleware(request)

    assert mock_logger.debug.call_count == 1
    assert "ValueError" == mock_logger.debug.call_args.args[1]
    assert "/analytics-error/" == mock_logger.debug.call_args.args[2]


def test_get_site_failure_does_not_break_response(monkeypatch):
    request = RequestFactory().get("/site-lookup-safe/")
    middleware = ViewHistoryMiddleware(lambda _request: HttpResponse("ok", status=200))

    mock_logger = Mock()
    create_mock = Mock()
    monkeypatch.setattr("apps.sites.middleware.logger", mock_logger)
    monkeypatch.setattr("apps.sites.middleware.ViewHistory.objects.create", create_mock)
    monkeypatch.setattr(
        "utils.sites.get_site",
        Mock(side_effect=OperationalError("site db unavailable")),
    )

    response = middleware(request)

    assert response.status_code == 200
    create_mock.assert_not_called()
    assert mock_logger.debug.call_count == 1
    assert "OperationalError" == mock_logger.debug.call_args.args[1]
    assert "/site-lookup-safe/" == mock_logger.debug.call_args.args[2]
