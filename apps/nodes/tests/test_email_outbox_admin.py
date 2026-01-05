import pytest
from unittest.mock import Mock

from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from apps.emails.admin import EmailOutboxAdminProxy
from apps.emails.models import EmailOutbox


@pytest.mark.django_db
def test_test_outboxes_action_sends_test_email(admin_user, monkeypatch):
    outbox = EmailOutbox.objects.create(
        host="smtp.example.com",
        username="outbox@example.com",
    )

    calls: list[tuple[EmailOutbox, tuple, dict]] = []

    def fake_send_mail(self, *args, **kwargs):
        calls.append((self, args, kwargs))

    monkeypatch.setattr(EmailOutbox, "send_mail", fake_send_mail)

    admin_view = EmailOutboxAdminProxy(EmailOutbox, admin.site)
    request = RequestFactory().post("/")
    request.user = admin_user
    request.session = {}
    setattr(request, "_messages", FallbackStorage(request))

    actions = admin_view.get_actions(request)
    assert "test_outboxes" in actions

    admin_view.test_outboxes(request, EmailOutbox.objects.filter(pk=outbox.pk))

    assert len(calls) == 1
    self_ref, args, kwargs = calls[0]
    assert self_ref.pk == outbox.pk
    assert args == ("Test email", "This is a test email.", [admin_user.email])
    assert kwargs == {}
