import pytest

from django.core.management import call_command
from django.test import RequestFactory

from apps.users.diagnostics import (
    build_diagnostic_bundle,
    capture_request_exception,
    create_manual_feedback,
)
from apps.users.models import User, UserDiagnosticBundle, UserDiagnosticEvent, UserDiagnosticsProfile


@pytest.mark.django_db
def test_capture_request_exception_creates_event_when_opted_in():
    user = User.objects.create_user(username="diag-user")
    UserDiagnosticsProfile.objects.create(
        user=user,
        is_enabled=True,
        collect_diagnostics=True,
        allow_manual_feedback=True,
    )
    request = RequestFactory().get("/ops/check?mode=diag")
    request.user = user

    capture_request_exception(request=request, exception=RuntimeError("boom"))

    event = UserDiagnosticEvent.objects.get()
    assert event.user == user
    assert event.source == UserDiagnosticEvent.Source.ERROR
    assert event.request_method == "GET"
    assert event.request_path == "/ops/check"
    assert event.fingerprint


@pytest.mark.django_db
def test_capture_request_exception_skips_when_collection_disabled():
    user = User.objects.create_user(username="diag-user-disabled")
    UserDiagnosticsProfile.objects.create(
        user=user,
        is_enabled=True,
        collect_diagnostics=False,
        allow_manual_feedback=True,
    )
    request = RequestFactory().post("/ops/fail")
    request.user = user

    capture_request_exception(request=request, exception=ValueError("not stored"))

    assert UserDiagnosticEvent.objects.count() == 0


@pytest.mark.django_db
def test_manual_feedback_and_bundle_generation_command():
    user = User.objects.create_user(username="diag-feedback")
    UserDiagnosticsProfile.objects.create(
        user=user,
        is_enabled=True,
        collect_diagnostics=True,
        allow_manual_feedback=True,
    )
    create_manual_feedback(
        user=user,
        summary="RFID login intermittently fails",
        details="Observed after software update on kiosk node.",
    )
    create_manual_feedback(
        user=user,
        summary="Manual note from support",
        details="Captured while reproducing with debug mode enabled.",
    )
    build_diagnostic_bundle(user=user, title="Pre-command bundle", limit=10)

    call_command(
        "diagnostics",
        "--username",
        user.username,
        "--title",
        "CLI bundle",
        "--limit",
        "10",
    )

    assert UserDiagnosticEvent.objects.count() == 2
    bundle = UserDiagnosticBundle.objects.filter(title="CLI bundle").first()
    assert bundle is not None
    assert bundle.events.count() == 2
    assert "fingerprint:" in bundle.report
