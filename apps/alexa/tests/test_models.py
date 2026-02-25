"""Model tests for Alexa reminders and account credentials."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.alexa.models import (
    AlexaAccount,
    AlexaCredentialsError,
    AlexaReminder,
    AlexaReminderDelivery,
)


@pytest.mark.django_db
def test_alexa_account_validate_credentials_requires_all_values():
    """Regression: account credential validation should fail for missing secrets."""

    user = get_user_model().objects.create_user(username="alexa-owner")
    account = AlexaAccount.objects.create(
        name="Office Alexa",
        user=user,
        client_id="client-id",
        client_secret="",
        refresh_token="refresh-token",
    )

    with pytest.raises(AlexaCredentialsError):
        account.validate_credentials()


@pytest.mark.django_db
def test_alexa_reminder_mark_event_update_only_marks_sent_like_deliveries():
    """Regression: event updates should not reset already-pending delivery records."""

    user = get_user_model().objects.create_user(username="alexa-reminder-owner")
    account = AlexaAccount.objects.create(
        name="Floor Alexa",
        user=user,
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
    )
    reminder = AlexaReminder.objects.create(
        name="Network maintenance",
        event_key="network.maintenance",
        content="Maintenance starts in 10 minutes",
        user=user,
        scheduled_for=timezone.now(),
    )

    pending_delivery = AlexaReminderDelivery.objects.create(
        reminder=reminder,
        account=account,
        status=AlexaReminderDelivery.STATUS_PENDING,
    )
    sent_delivery = AlexaReminderDelivery.objects.create(
        reminder=reminder,
        account=AlexaAccount.objects.create(
            name="Lobby Alexa",
            user=user,
            client_id="client-id-2",
            client_secret="client-secret-2",
            refresh_token="refresh-token-2",
        ),
        status=AlexaReminderDelivery.STATUS_SENT,
    )

    updated = reminder.mark_event_update()

    pending_delivery.refresh_from_db()
    sent_delivery.refresh_from_db()
    assert updated == 1
    assert pending_delivery.status == AlexaReminderDelivery.STATUS_PENDING
    assert sent_delivery.status == AlexaReminderDelivery.STATUS_UPDATE_PENDING
