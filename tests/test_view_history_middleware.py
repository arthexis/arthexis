from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import override_settings

from pages.middleware import ViewHistoryMiddleware
from pages.models import Application, Landing, LandingLead, Module, ViewHistory
from nodes.models import NodeRole


@override_settings(STATIC_URL="/static/", MEDIA_URL="/media/")
def test_view_history_should_track_filters_expected_paths():
    rf = RequestFactory()
    middleware = ViewHistoryMiddleware(lambda request: HttpResponse("ok"))

    assert not middleware._should_track(rf.post("/public/path/"))
    assert not middleware._should_track(rf.get("/admin/dashboard/"))
    assert not middleware._should_track(rf.get("/static/app.js"))
    assert not middleware._should_track(rf.get("/robots.txt"))
    assert not middleware._should_track(rf.get("/landing/?djdt=1"))
    assert middleware._should_track(rf.get("/landing/"))


@pytest.mark.django_db
@patch("pages.middleware.get_original_referer", return_value="https://example.com/previous")
@patch("pages.middleware.landing_leads_supported", return_value=True)
def test_view_history_records_landing_lead(mock_supported, mock_referer):
    rf = RequestFactory()
    response = HttpResponse("ok")
    middleware = ViewHistoryMiddleware(lambda request: response)

    role = NodeRole.objects.create(name="Public")
    app = Application.objects.create(name="PublicApp")
    module = Module.objects.create(node_role=role, application=app, path="/landing/")
    landing = module.landings.get()
    landing.track_leads = True
    landing.save(update_fields=["track_leads"])

    user = get_user_model().objects.create_user(username="alice", password="secret")
    request = rf.get("/landing/?utm=1")
    request.user = user
    request.session = {}
    request.META["HTTP_USER_AGENT"] = "pytest"
    request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.10"
    request.resolver_match = SimpleNamespace(view_name="pages:landing", func=lambda: None)

    middleware._record_visit(request, 200, "")

    history = ViewHistory.objects.get()
    assert history.path == request.get_full_path()
    assert history.status_code == 200
    assert history.view_name == "pages:landing"

    lead = LandingLead.objects.get()
    assert lead.landing == landing
    assert lead.user == user
    assert lead.referer == "https://example.com/previous"
    assert lead.user_agent == "pytest"
    assert lead.ip_address == "198.51.100.10"

    user.refresh_from_db()
    assert user.last_visit_ip_address == "198.51.100.10"

    mock_supported.assert_called_once()
    mock_referer.assert_called_once()
