"""Tests for :mod:`core.mailer`."""

from __future__ import annotations

import os
from types import SimpleNamespace

import django
from django.core import mail
from django.test.utils import override_settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


class DummyManager:
    """Minimal manager mimicking the queryset API used by ``can_send_email``."""

    def __init__(self, exists: bool) -> None:
        self._exists = exists

    def filter(self, *args, **kwargs):  # pragma: no cover - passthrough
        return self

    def exclude(self, *args, **kwargs):  # pragma: no cover - passthrough
        return self

    def exists(self) -> bool:
        return self._exists


def test_can_send_email_respects_dummy_backend(monkeypatch):
    """``can_send_email`` should return ``False`` when using the dummy backend."""

    from core import mailer
    from nodes import models as nodes_models

    monkeypatch.setattr(
        nodes_models,
        "EmailOutbox",
        SimpleNamespace(objects=DummyManager(False)),
        raising=False,
    )

    with override_settings(
        EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend"
    ):
        assert mailer.can_send_email() is False


def test_can_send_email_detects_outbox(monkeypatch):
    """When an outbox is enabled, ``can_send_email`` should return ``True``."""

    from core import mailer
    from nodes import models as nodes_models

    monkeypatch.setattr(
        nodes_models,
        "EmailOutbox",
        SimpleNamespace(objects=DummyManager(True)),
        raising=False,
    )

    with override_settings(
        EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend"
    ):
        assert mailer.can_send_email() is True


def test_send_allows_two_item_attachment():
    """``send`` should accept two-part attachments without raising errors."""

    from core import mailer

    with override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="noreply@example.com",
    ):
        mail.get_connection()
        mail.outbox.clear()
        email = mailer.send(
            "Subject",
            "Body",
            ["person@example.com"],
            attachments=[("report.txt", "hello world")],
            fail_silently=True,
        )

        attachment = email.attachments[0]
        assert attachment.filename == "report.txt"
        assert attachment.content == "hello world"
        assert attachment.mimetype == "text/plain"

        stored_attachment = mail.outbox[0].attachments[0]
        assert stored_attachment.filename == "report.txt"
        assert stored_attachment.content == "hello world"
        assert stored_attachment.mimetype == "text/plain"
